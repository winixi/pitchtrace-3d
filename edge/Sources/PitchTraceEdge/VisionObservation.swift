import CoreGraphics
import CoreML
import Foundation
import ImageIO
import Vision
import UniformTypeIdentifiers

struct ObservedPlayer: Encodable {
    let box: [Double]
    let confidence: Float
    var joints: [String: [Double]] = [:]
    var foot: [Double]?
    var number: Int?
    var shirtRGB: [Int]?
    // 号码已稳定的轨迹本帧未送读号；与读号失败区分，number 仍为缺测。
    var ocrSkipped: Bool?
    // 过载降级时本帧未估计姿态；foot 改由人框下沿估计并标注来源。
    var poseSkipped: Bool?
    var footSource: String?
}

struct ObservedBall: Encodable {
    let pixel: [Double]
    let box: [Double]
    let confidence: Float
    let source: String
}

// 配置名与比赛配置 detector.computeUnits 一致，便于对照 Xcode 性能报告。
let modelComputeUnits: [String: MLComputeUnits] = [
    "all": .all, "cpuAndNeuralEngine": .cpuAndNeuralEngine, "cpuAndGPU": .cpuAndGPU, "cpuOnly": .cpuOnly
]

struct VisionWorkerOptions {
    let modelURL: URL?
    let personAndBall: Bool
    let ballSearch: Bool
    let individualOCR: Bool
    let ocrHints: Bool
    let frameControl: Bool
    let computeUnits: MLComputeUnits

    static let usage = "用法：vision-worker [模型路径 [--person-and-ball] [--ball-search]] [--individual-ocr] [--ocr-hints] [--frame-control] [--compute-units all|cpuAndNeuralEngine|cpuAndGPU|cpuOnly]"

    init(arguments: [String]) throws {
        var args = arguments[...]
        if let first = args.first, !first.isEmpty, !first.hasPrefix("--") {
            modelURL = URL(fileURLWithPath: first)
            args = args.dropFirst()
        } else {
            modelURL = nil
        }
        personAndBall = args.first == "--person-and-ball"
        if personAndBall { args = args.dropFirst() }
        ballSearch = args.first == "--ball-search"
        if ballSearch { args = args.dropFirst() }
        individualOCR = args.first == "--individual-ocr"
        if individualOCR { args = args.dropFirst() }
        ocrHints = args.first == "--ocr-hints"
        if ocrHints { args = args.dropFirst() }
        frameControl = args.first == "--frame-control"
        if frameControl { args = args.dropFirst() }
        var units: MLComputeUnits?
        if args.first == "--compute-units" {
            args = args.dropFirst()
            guard let name = args.first, let value = modelComputeUnits[name] else {
                throw RecordingError(Self.usage)
            }
            units = value
            args = args.dropFirst()
        }
        computeUnits = units ?? .all
        // 计算单元只作用于 Core ML 检测器，系统 Vision 请求仍由 Vision 自行调度。
        guard args.isEmpty, modelURL != nil || !(personAndBall || ballSearch || units != nil) else {
            throw RecordingError(Self.usage)
        }
    }
}

private struct ImageObservation: Encodable {
    let imageSize: [Int]
    let players: [ObservedPlayer]
    let ball: ObservedBall?

    let timing: VisionTiming?

    enum CodingKeys: CodingKey { case imageSize, players, ball, timing }
    func encode(to encoder: Encoder) throws {
        var keyed = encoder.container(keyedBy: CodingKeys.self)
        try keyed.encode(imageSize, forKey: .imageSize)
        try keyed.encode(players, forKey: .players)
        try keyed.encode(ball, forKey: .ball)
        try keyed.encodeIfPresent(timing, forKey: .timing)
    }
}

// 只接受能明确归于同一号码的数字，保留合法的 0 号。
func jerseyNumber(_ candidates: [String]) -> Int? {
    let values = Set(candidates.compactMap { text -> Int? in
        let digits = text.trimmingCharacters(in: .whitespacesAndNewlines).utf8
        guard (1...2).contains(digits.count), digits.allSatisfy({ (48...57).contains($0) })
        else { return nil }
        return Int(String(decoding: digits, as: UTF8.self))
    })
    return values.count == 1 ? values.first : nil
}

private let bodyJoints: [(VNHumanBodyPoseObservation.JointName, String)] = [
    (.nose, "nose"), (.neck, "neck"), (.root, "root"),
    (.leftEye, "leftEye"), (.rightEye, "rightEye"), (.leftEar, "leftEar"), (.rightEar, "rightEar"),
    (.leftShoulder, "leftShoulder"), (.rightShoulder, "rightShoulder"),
    (.leftElbow, "leftElbow"), (.rightElbow, "rightElbow"),
    (.leftWrist, "leftWrist"), (.rightWrist, "rightWrist"),
    (.leftHip, "leftHip"), (.rightHip, "rightHip"),
    (.leftKnee, "leftKnee"), (.rightKnee, "rightKnee"),
    (.leftAnkle, "leftAnkle"), (.rightAnkle, "rightAnkle")
]

// Vision 以裁片左下为原点。输出使用原图左上像素坐标。
func originalPixel(_ point: CGPoint, in crop: CGRect) -> [Double]? {
    guard (0...1).contains(point.x), (0...1).contains(point.y) else { return nil }
    return [
        crop.minX + point.x * (crop.width - 1),
        crop.minY + (1 - point.y) * (crop.height - 1)
    ]
}

private func pixelBox(_ box: CGRect, in region: CGRect) -> CGRect? {
    guard box.origin.x.isFinite, box.origin.y.isFinite, box.size.width.isFinite,
          box.size.height.isFinite, box.size.width > 0, box.size.height > 0 else { return nil }
    // Vision 已将 scaleFit 的填边换回送入请求的图像坐标，此处只还原裁片偏移和原点。
    let result = CGRect(x: region.minX + box.minX * region.width,
                        y: region.minY + (1 - box.maxY) * region.height,
                        width: box.width * region.width, height: box.height * region.height)
        .integral.intersection(region)
    return result.isEmpty ? nil : result
}

struct SelectedDetections {
    var people: [(box: CGRect, confidence: Float)] = []
    var ball: ObservedBall?

    mutating func includePerson(confidence: Float, bounds: CGRect, in region: CGRect) {
        guard (0.35...1).contains(confidence), let box = pixelBox(bounds, in: region),
              box.width >= 2, box.height >= 2 else { return }
        people.append((box, confidence))
    }

    mutating func include(label: String, confidence: Float, bounds: CGRect, in region: CGRect,
                          includePeople: Bool, ballSource: String) {
        switch label.lowercased() {
        case "person" where includePeople:
            includePerson(confidence: confidence, bounds: bounds, in: region)
        case "sports ball", "soccer ball", "football", "ball":
            guard (0.35...1).contains(confidence), confidence > (ball?.confidence ?? 0),
                  let box = pixelBox(bounds, in: region) else { return }
            ball = ObservedBall(pixel: [box.midX, box.midY], box: [box.minX, box.minY, box.width, box.height],
                                confidence: confidence, source: ballSource)
        default: break
        }
    }
}

private func shirtColor(_ image: CGImage) -> [Int]? {
    var bytes = [UInt8](repeating: 0, count: 4)
    let sampled = bytes.withUnsafeMutableBytes { buffer -> Bool in
        guard let context = CGContext(
            data: buffer.baseAddress, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue | CGBitmapInfo.byteOrder32Big.rawValue
        ) else { return false }
        context.interpolationQuality = .high
        context.draw(image, in: CGRect(x: 0, y: 0, width: 1, height: 1))
        return true
    }
    return sampled ? bytes.prefix(3).map(Int.init) : nil
}

private enum VisionStage { case detection, pose, ocr }

private final class VisionTiming: Encodable {
    var detectionNs: UInt64 = 0
    var poseNs: UInt64 = 0
    var ocrNs: UInt64 = 0
    var ocrSheetNs: UInt64 = 0
    var detectionRequests = 0
    var poseRequests = 0
    var ocrRequests = 0

    func record(_ stage: VisionStage, duration: UInt64, requests: Int) {
        switch stage {
        case .detection:
            detectionNs += duration
            detectionRequests += requests
        case .pose:
            poseNs += duration
            poseRequests += requests
        case .ocr:
            ocrNs += duration
            ocrRequests += requests
        }
    }
}

private func performVision(_ requests: [VNRequest], on image: CGImage,
                           stage: VisionStage, timing: VisionTiming?) throws {
    let begin = timing.map { _ in DispatchTime.now().uptimeNanoseconds }
    defer {
        if let begin { timing?.record(stage, duration: DispatchTime.now().uptimeNanoseconds - begin, requests: requests.count) }
    }
    do {
        try VNImageRequestHandler(cgImage: image, options: [:]).perform(requests)
    } catch {
        // 无法使用加速器时，按各请求实际支持的计算设备尝试软件执行。
        var switched = false
        for request in requests {
            for (stage, devices) in try request.supportedComputeStageDevices {
                guard let cpu = devices.first(where: { if case .cpu = $0 { true } else { false } }),
                      request.computeDevice(for: stage) != cpu else { continue }
                request.setComputeDevice(cpu, for: stage)
                switched = true
            }
        }
        guard switched else { throw error }
        try VNImageRequestHandler(cgImage: image, options: [:]).perform(requests)
    }
}

// 使用统一像素比例排版，保留躯干裁片之间的空白。文字框必须完整属于一个人。
private struct ShirtCrop {
    let player: Int
    let image: CGImage
}

func shirtOwner(of text: CGRect, in tiles: [CGRect]) -> Int? {
    guard !text.isEmpty else { return nil }
    return tiles.firstIndex { $0.contains(text) }
}

// 原图中央有充足像素和强对比笔画时，保留高分辨率的单人读号机会。
private func prefersIndividualOCR(_ shirt: CGImage) -> Bool {
    guard min(shirt.width, shirt.height) >= 32 else { return false }
    let side = 16
    var gray = [UInt8](repeating: 0, count: side * side)
    let drawn = gray.withUnsafeMutableBytes { buffer -> Bool in
        guard let context = CGContext(data: buffer.baseAddress, width: side, height: side,
                                      bitsPerComponent: 8, bytesPerRow: side,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
        else { return false }
        context.interpolationQuality = .medium
        context.draw(shirt, in: CGRect(x: 0, y: 0, width: side, height: side))
        return true
    }
    guard drawn else { return true }
    // 忽略边缘草地和极少数的高光，以中央亮度的 10–90 百分位衡量对比。
    let center = (2..<14).flatMap { y in gray[(y * side + 2)..<(y * side + 14)] }.sorted()
    return Int(center[center.count * 9 / 10]) - Int(center[center.count / 10]) >= 128
}

private func numberSheet(_ crops: ArraySlice<ShirtCrop>) -> (CGImage, [CGRect])? {
    let padding = 16, scale = 3
    let tileW = crops.map { $0.image.width }.max()! * scale + 2 * padding
    let tileH = crops.map { $0.image.height }.max()! * scale + 2 * padding
    let cols = min(crops.count, 3)
    let w = cols * tileW, h = ((crops.count + cols - 1) / cols) * tileH
    guard let context = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
    context.setFillColor(gray: 0.5, alpha: 1)
    context.fill(CGRect(x: 0, y: 0, width: w, height: h))
    context.interpolationQuality = .high
    var tiles: [CGRect] = []
    for (i, crop) in crops.enumerated() {
        let rect = CGRect(x: (i % cols) * tileW + padding, y: (i / cols) * tileH + padding,
                          width: crop.image.width * scale, height: crop.image.height * scale)
        context.draw(crop.image, in: rect)
        // Core Graphics 与 Vision 的相对坐标都从左下算起。
        tiles.append(rect.applying(CGAffineTransform(scaleX: 1 / Double(w), y: 1 / Double(h))))
    }
    return context.makeImage().map { ($0, tiles) }
}

private final class ImageObserver {
    private let individualOCR: Bool
    private let rectangles: VNDetectHumanRectanglesRequest?
    private let measuresTiming = ProcessInfo.processInfo.environment["PITCHTRACE_VISION_TIMING"] == "1"
    private let pose = VNDetectHumanBodyPoseRequest()
    private let ocr = VNRecognizeTextRequest()
    private let objects: VNCoreMLRequest?
    private var temporaryModel: URL?

    init(options: VisionWorkerOptions) throws {
        individualOCR = options.individualOCR
        rectangles = options.personAndBall ? nil : VNDetectHumanRectanglesRequest()
        rectangles?.upperBodyOnly = false
        ocr.recognitionLevel = .accurate
        ocr.usesLanguageCorrection = false
        ocr.recognitionLanguages = ["en-US"]
        guard let modelURL = options.modelURL else {
            objects = nil
            return
        }
        let compiled: URL
        if modelURL.pathExtension.lowercased() == "mlmodelc" {
            compiled = modelURL
        } else {
            compiled = try MLModel.compileModel(at: modelURL)
            temporaryModel = compiled
        }
        do {
            let configuration = MLModelConfiguration()
            configuration.computeUnits = options.computeUnits
            let model = try MLModel(contentsOf: compiled, configuration: configuration)
            // Vision 识别带 NMS 的模型需要框和各类置信度两组输出。
            let description = model.modelDescription
            guard description.predictedFeatureName == nil,
                  description.outputDescriptionsByName.values.filter({ $0.type == .multiArray }).count >= 2 else {
                throw RecordingError("Core ML 模型须包含检测框和类别置信度输出")
            }
            let request = try VNCoreMLRequest(model: VNCoreMLModel(for: model))
            request.imageCropAndScaleOption = .scaleFit
            objects = request
        } catch {
            if let temporaryModel { try? FileManager.default.removeItem(at: temporaryModel) }
            throw error
        }
    }

    deinit {
        if let temporaryModel { try? FileManager.default.removeItem(at: temporaryModel) }
    }

    private func selectedResults(_ request: VNCoreMLRequest, in region: CGRect,
                                 includePeople: Bool, source: String) throws -> SelectedDetections {
        guard let detections = request.results as? [VNRecognizedObjectObservation] else {
            throw RecordingError("Core ML 模型须输出 Vision 可读取的带类别检测框")
        }
        var selected = SelectedDetections()
        for detection in detections {
            if let label = detection.labels.first {
                // NMS 检测器的框分在 observation 上，类别分只用于选标签。
                selected.include(label: label.identifier, confidence: detection.confidence,
                                 bounds: detection.boundingBox, in: region,
                                 includePeople: includePeople, ballSource: source)
            }
        }
        return selected
    }

    private func readNumbers(_ crops: [ShirtCrop], players: inout [ObservedPlayer], timing: VisionTiming?) throws {
        var small: [ShirtCrop] = []
        func readSingle(_ crop: ShirtCrop) throws {
            try performVision([ocr], on: crop.image, stage: .ocr, timing: timing)
            let texts = (ocr.results ?? []).compactMap { $0.topCandidates(1).first }
            players[crop.player].number = jerseyNumber(texts.filter { $0.confidence >= 0.65 }.map(\.string))
        }
        for crop in crops {
            if !individualOCR && crop.image.width <= 96 && crop.image.height <= 96 && !prefersIndividualOCR(crop.image) {
                small.append(crop)
            } else {
                try readSingle(crop)
            }
        }
        for start in stride(from: 0, to: small.count, by: 6) {
            let batch = small[start..<min(start + 6, small.count)]
            let startSheet = timing.map { _ in DispatchTime.now().uptimeNanoseconds }
            let prepared = batch.count > 1 ? numberSheet(batch) : nil
            if let startSheet { timing?.ocrSheetNs += DispatchTime.now().uptimeNanoseconds - startSheet }
            guard let (sheet, tiles) = prepared else {
                for crop in batch { try readSingle(crop) }
                continue
            }
            try performVision([ocr], on: sheet, stage: .ocr, timing: timing)
            var strings = Array(repeating: [String](), count: batch.count)
            for observation in ocr.results ?? [] {
                if let owner = shirtOwner(of: observation.boundingBox, in: tiles),
                   let text = observation.topCandidates(1).first, text.confidence >= 0.65 {
                    strings[owner].append(text.string)
                }
            }
            for (i, crop) in batch.enumerated() {
                players[crop.player].number = jerseyNumber(strings[i])
            }
        }
    }

    func observe(_ data: Data, ballSearch: CGRect?, ocrSkip: [CGRect] = [],
                 control: FrameControl = FrameControl()) throws -> ImageObservation {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              CGImageSourceGetType(source) as String? == UTType.jpeg.identifier,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = properties[kCGImagePropertyPixelWidth] as? Int,
              let h = properties[kCGImagePropertyPixelHeight] as? Int,
              (1...16384).contains(w), (1...16384).contains(h), w * h <= 48_000_000,
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { throw RecordingError("视觉输入必须为可解码的 JPEG 原图，尺寸超过上限或数据无效") }
        let frame = CGRect(x: 0, y: 0, width: w, height: h)
        if let ballSearch, !frame.contains(ballSearch) {
            throw RecordingError("足球搜索裁片超出原图")
        }
        let timing = measuresTiming ? VisionTiming() : nil
        let requests: [VNRequest] = [rectangles, objects].compactMap { $0 }
        try performVision(requests, on: image, stage: .detection, timing: timing)
        var selected = SelectedDetections()
        if let objects {
            selected = try selectedResults(objects, in: frame, includePeople: rectangles == nil, source: "coreML")
            if selected.ball == nil, let ballSearch {
                guard let crop = image.cropping(to: ballSearch) else {
                    throw RecordingError("无法提取足球搜索裁片")
                }
                try performVision([objects], on: crop, stage: .detection, timing: timing)
                selected.ball = try selectedResults(objects, in: ballSearch, includePeople: false,
                                                    source: "coreML-ROI").ball
            }
        }
        for person in rectangles?.results ?? [] {
            selected.includePerson(confidence: person.confidence, bounds: person.boundingBox, in: frame)
        }
        var players: [ObservedPlayer] = []
        var shirts: [ShirtCrop] = []
        let boxes = selected.people.map(\.box)
        for (i, person) in selected.people.enumerated() {
            let box = person.box
            guard let crop = image.cropping(to: box) else { continue }
            var player = ObservedPlayer(box: [box.minX, box.minY, box.width, box.height],
                                        confidence: person.confidence)
            if !control.estimatesPose(for: box) {
                player.poseSkipped = true
                // 降级时以人框下沿中点近似鞋底；画面截脚时仍保留缺测。
                if box.maxY < Double(h) {
                    player.foot = [box.midX, box.maxY - 1]
                    player.footSource = "box"
                }
            } else {
                try performVision([pose], on: crop, stage: .pose, timing: timing)
                if let poses = pose.results, poses.count == 1 {
                    let points = try poses[0].recognizedPoints(.all)
                    for (key, name) in bodyJoints {
                        if let point = points[key], point.confidence >= 0.3 {
                            player.joints[name] = originalPixel(point.location, in: box)
                        }
                    }
                    // 脚踝须落在人体框下端才用框底估计鞋底。身下留空或画面截脚时保留缺测。
                    let ankles = ["leftAnkle", "rightAnkle"].compactMap { player.joints[$0] }
                    if let ankle = ankles.max(by: { $0[1] < $1[1] }),
                       box.maxY < Double(h), box.maxY - ankle[1] <= 0.15 * box.height {
                        player.foot = [ankle[0], box.maxY - 1]
                    }
                }
            }
            let torso = CGRect(x: box.minX + 0.22 * box.width, y: box.minY + 0.18 * box.height,
                               width: 0.56 * box.width, height: 0.35 * box.height).integral
            let overlaps = boxes.indices.contains { $0 != i && boxes[$0].intersects(torso) }
            let skipped = control.skipOCR || skipsOCR(box, hints: ocrSkip)
            if skipped { player.ocrSkipped = true }
            if !overlaps, let shirt = image.cropping(to: torso) {
                player.shirtRGB = shirtColor(shirt)
                if !skipped, shirt.width >= 12, shirt.height >= 12 {
                    shirts.append(ShirtCrop(player: players.count, image: shirt))
                }
            }
            players.append(player)
        }
        try readNumbers(shirts, players: &players, timing: timing)
        return ImageObservation(imageSize: [w, h], players: players, ball: selected.ball, timing: timing)
    }
}

/// 过载降级的逐帧控制。`poseCenter` 为空时全员估计姿态；半径为零表示本帧不估计姿态。
struct FrameControl: Equatable {
    var skipOCR = false
    var poseCenter: CGPoint?
    var poseRadius: Double = 0

    func estimatesPose(for box: CGRect) -> Bool {
        guard let poseCenter else { return true }
        return hypot(box.midX - poseCenter.x, box.midY - poseCenter.y) <= poseRadius
    }
}

struct VisionPacket {
    let image: Data
    let ballSearch: CGRect?
    var ocrSkip: [CGRect] = []
    var control = FrameControl()
}

let maximumOCRHints = 64

func readVisionPacket(from input: FileHandle, ballSearch: Bool = false, ocrHints: Bool = false,
                      frameControl: Bool = false) throws -> VisionPacket? {
    var started = false
    func read(_ size: Int) throws -> Data? {
        var bytes = Data()
        while bytes.count < size {
            guard let part = try input.read(upToCount: size - bytes.count), !part.isEmpty else {
                // 只允许在帧边界结束输入。
                if !started && bytes.isEmpty { return nil }
                throw RecordingError("视觉输入在 JPEG 包中途截断")
            }
            bytes.append(part)
            started = true
        }
        return bytes
    }
    func integer(_ bytes: Data) -> Int {
        bytes.reduce(0) { $0 * 256 + Int($1) }
    }
    func box(_ bytes: Data) -> CGRect {
        let base = bytes.startIndex
        let v = (0..<4).map { integer(bytes[(base + 4 * $0)..<(base + 4 * $0 + 4)]) }
        return CGRect(x: v[0], y: v[1], width: v[2], height: v[3])
    }
    var region: CGRect?
    if ballSearch {
        guard let header = try read(16) else { return nil }
        if header.contains(where: { $0 != 0 }) {
            region = box(header)
            guard region!.width > 0, region!.height > 0 else { throw RecordingError("足球搜索裁片的宽高必须大于零") }
        }
    }
    var skip: [CGRect] = []
    if ocrHints {
        guard let header = try read(4) else { return nil }
        let hints = integer(header)
        guard hints <= maximumOCRHints else { throw RecordingError("免读号区域数量超出上限 \(maximumOCRHints)") }
        if hints > 0 {
            let bytes = try read(16 * hints)!
            skip = (0..<hints).map { box(bytes[(bytes.startIndex + 16 * $0)..<(bytes.startIndex + 16 * $0 + 16)]) }
            guard skip.allSatisfy({ $0.width > 0 && $0.height > 0 }) else {
                throw RecordingError("免读号区域的宽高必须大于零")
            }
        }
    }
    var control = FrameControl()
    if frameControl {
        // [标志, 球心 x, 球心 y, 半径]：标志第 0 位停读号，第 1 位只为球附近的人估计姿态。
        guard let block = try read(16) else { return nil }
        let base = block.startIndex
        let v = (0..<4).map { integer(block[(base + 4 * $0)..<(base + 4 * $0 + 4)]) }
        guard v[0] & ~3 == 0 else { throw RecordingError("逐帧控制含未知标志") }
        control.skipOCR = v[0] & 1 != 0
        if v[0] & 2 != 0 {
            control.poseCenter = CGPoint(x: v[1], y: v[2])
            control.poseRadius = Double(v[3])
        }
    }
    guard let length = try read(4) else { return nil }
    let count = integer(length)
    guard count > 0, count <= 32 * 1024 * 1024 else { throw RecordingError("JPEG 包长度超出范围") }
    guard let image = try read(count) else { throw RecordingError("JPEG 包缺少图像") }
    return VisionPacket(image: image, ballSearch: region, ocrSkip: skip, control: control)
}

// 上一曝光中号码已稳定的人框与本帧人框交并比达到一半，才视为同一人并跳过读号。
func skipsOCR(_ box: CGRect, hints: [CGRect]) -> Bool {
    hints.contains { hint in
        let overlap = hint.intersection(box)
        guard !overlap.isNull, !overlap.isEmpty else { return false }
        let shared = overlap.width * overlap.height
        return shared / (box.width * box.height + hint.width * hint.height - shared) >= 0.5
    }
}

func runVisionWorker(options: VisionWorkerOptions) throws {
    let observer = try ImageObserver(options: options)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    while let packet = try readVisionPacket(from: .standardInput, ballSearch: options.ballSearch,
                                            ocrHints: options.ocrHints, frameControl: options.frameControl) {
        try autoreleasepool {
            var line = try encoder.encode(observer.observe(packet.image, ballSearch: packet.ballSearch,
                                                           ocrSkip: packet.ocrSkip, control: packet.control))
            line.append(0x0a)
            try FileHandle.standardOutput.write(contentsOf: line)
        }
    }
}
