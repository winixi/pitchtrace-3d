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
}

struct ObservedBall: Encodable {
    let pixel: [Double]
    let confidence: Float
}

private struct ImageObservation: Encodable {
    let imageSize: [Int]
    let players: [ObservedPlayer]
    let ball: ObservedBall?

    enum CodingKeys: CodingKey { case imageSize, players, ball }
    func encode(to encoder: Encoder) throws {
        var keyed = encoder.container(keyedBy: CodingKeys.self)
        try keyed.encode(imageSize, forKey: .imageSize)
        try keyed.encode(players, forKey: .players)
        try keyed.encode(ball, forKey: .ball)
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

private func pixelBox(_ box: CGRect, in image: CGImage) -> CGRect {
    let width = CGFloat(image.width), height = CGFloat(image.height)
    return CGRect(x: box.minX * width, y: (1 - box.maxY) * height,
                  width: box.width * width, height: box.height * height)
        .integral.intersection(CGRect(x: 0, y: 0, width: width, height: height))
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

private func performVision(_ requests: [VNRequest], on image: CGImage) throws {
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

private final class ImageObserver {
    private let rectangles = VNDetectHumanRectanglesRequest()
    private let pose = VNDetectHumanBodyPoseRequest()
    private let ocr = VNRecognizeTextRequest()
    private let objects: VNCoreMLRequest?
    private var temporaryModel: URL?

    init(modelURL: URL?) throws {
        rectangles.upperBodyOnly = false
        ocr.recognitionLevel = .accurate
        ocr.usesLanguageCorrection = false
        ocr.recognitionLanguages = ["en-US"]
        guard let modelURL else {
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
            let model = try MLModel(contentsOf: compiled, configuration: MLModelConfiguration())
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

    func observe(_ data: Data) throws -> ImageObservation {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              CGImageSourceGetType(source) as String? == UTType.jpeg.identifier,
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let w = properties[kCGImagePropertyPixelWidth] as? Int,
              let h = properties[kCGImagePropertyPixelHeight] as? Int,
              (1...16384).contains(w), (1...16384).contains(h), w * h <= 48_000_000,
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        else { throw RecordingError("视觉输入必须为可解码的 JPEG 原图，尺寸超过上限或数据无效") }
        try performVision([rectangles] + (objects.map { [$0] } ?? []), on: image)
        var ball: ObservedBall?
        if let objects {
            guard let detections = objects.results as? [VNRecognizedObjectObservation] else {
                throw RecordingError("足球模型须输出 Vision 可读取的带类别检测框")
            }
            for detection in detections {
                guard let label = detection.labels.first,
                      ["sports ball", "soccer ball", "football", "ball"].contains(label.identifier.lowercased()),
                      label.confidence >= 0.35, label.confidence > (ball?.confidence ?? 0)
                else { continue }
                let box = pixelBox(detection.boundingBox, in: image)
                guard !box.isEmpty else { continue }
                ball = ObservedBall(pixel: [box.midX, box.midY], confidence: label.confidence)
            }
        }
        var players: [ObservedPlayer] = []
        let people = (rectangles.results ?? []).filter { $0.confidence >= 0.35 }
        let boxes = people.map { pixelBox($0.boundingBox, in: image) }
        for (i, person) in people.enumerated() {
            let box = boxes[i]
            guard box.width >= 2, box.height >= 2, let crop = image.cropping(to: box) else { continue }
            var player = ObservedPlayer(box: [box.minX, box.minY, box.width, box.height],
                                        confidence: person.confidence)
            try performVision([pose], on: crop)
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
            let torso = CGRect(x: box.minX + 0.22 * box.width, y: box.minY + 0.18 * box.height,
                               width: 0.56 * box.width, height: 0.35 * box.height).integral
            let overlaps = boxes.indices.contains { $0 != i && boxes[$0].intersects(torso) }
            if !overlaps, let shirt = image.cropping(to: torso) {
                player.shirtRGB = shirtColor(shirt)
                if shirt.width >= 12, shirt.height >= 12 {
                    try performVision([ocr], on: shirt)
                    let texts = (ocr.results ?? []).compactMap { $0.topCandidates(1).first }
                    player.number = jerseyNumber(texts.filter { $0.confidence >= 0.65 }.map(\.string))
                }
            }
            players.append(player)
        }
        return ImageObservation(imageSize: [w, h], players: players, ball: ball)
    }
}

func readVisionPacket(from input: FileHandle) throws -> Data? {
    func read(_ size: Int, allowEOF: Bool = false) throws -> Data? {
        var bytes = Data()
        while bytes.count < size {
            guard let part = try input.read(upToCount: size - bytes.count), !part.isEmpty else {
                if allowEOF && bytes.isEmpty { return nil }
                throw RecordingError("视觉输入在 JPEG 包中途截断")
            }
            bytes.append(part)
        }
        return bytes
    }
    guard let header = try read(4, allowEOF: true) else { return nil }
    let count = header.reduce(0) { $0 * 256 + Int($1) }
    guard count > 0, count <= 32 * 1024 * 1024 else { throw RecordingError("JPEG 包长度超出范围") }
    return try read(count)
}

func runVisionWorker(modelURL: URL?) throws {
    let observer = try ImageObserver(modelURL: modelURL)
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    while let packet = try readVisionPacket(from: .standardInput) {
        try autoreleasepool {
            var line = try encoder.encode(observer.observe(packet))
            line.append(0x0a)
            try FileHandle.standardOutput.write(contentsOf: line)
        }
    }
}
