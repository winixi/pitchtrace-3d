import Foundation

/// 把相机原图转为 BGRA 8 位，供 VideoToolbox 编码。Bayer RG 8 用双线性插值，边缘按镜像取邻点以保留色彩相位。
func convertToBGRA(_ frame: CameraFrame, into destination: UnsafeMutableRawPointer, bytesPerRow: Int) throws {
    let w = frame.width, h = frame.height
    guard w >= 2, h >= 2, frame.pixels.count == w * h, bytesPerRow >= 4 * w else {
        throw RecordingError("原图尺寸与像素数据不符")
    }
    if frame.pixelFormat == .bayerRG8 && (w % 2 != 0 || h % 2 != 0) {
        throw RecordingError("Bayer 原图的宽高须为偶数")
    }
    let output = destination.assumingMemoryBound(to: UInt8.self)
    frame.pixels.withUnsafeBytes { raw in
        let src = raw.bindMemory(to: UInt8.self).baseAddress!
        switch frame.pixelFormat {
        case .mono8:
            for y in 0..<h {
                let row = src + y * w, out = output + y * bytesPerRow
                for x in 0..<w {
                    let v = row[x]
                    out[4 * x] = v; out[4 * x + 1] = v; out[4 * x + 2] = v; out[4 * x + 3] = 255
                }
            }
        case .bayerRG8:
            demosaicRGGB(src, width: w, height: h, into: output, bytesPerRow: bytesPerRow)
        }
    }
}

private func demosaicRGGB(_ src: UnsafePointer<UInt8>, width w: Int, height h: Int,
                          into output: UnsafeMutablePointer<UInt8>, bytesPerRow: Int) {
    // 镜像：-1 → 1，n → n−2，奇偶性不变，邻点仍是同一色彩通道。
    @inline(__always) func reflect(_ i: Int, _ n: Int) -> Int { i < 0 ? -i : (i >= n ? 2 * n - 2 - i : i) }
    for y in 0..<h {
        let up = src + reflect(y - 1, h) * w, row = src + y * w, down = src + reflect(y + 1, h) * w
        let out = output + y * bytesPerRow
        let redRow = y % 2 == 0
        @inline(__always) func pixel(_ x: Int, _ l: Int, _ r: Int) {
            let c = Int(row[x])
            let cross = (Int(row[l]) + Int(row[r]) + Int(up[x]) + Int(down[x]) + 2) >> 2
            let diagonal = (Int(up[l]) + Int(up[r]) + Int(down[l]) + Int(down[r]) + 2) >> 2
            let horizontal = (Int(row[l]) + Int(row[r]) + 1) >> 1
            let vertical = (Int(up[x]) + Int(down[x]) + 1) >> 1
            let red: Int, green: Int, blue: Int
            switch (redRow, x % 2 == 0) {
            case (true, true): (red, green, blue) = (c, cross, diagonal)
            case (true, false): (red, green, blue) = (horizontal, c, vertical)
            case (false, true): (red, green, blue) = (vertical, c, horizontal)
            case (false, false): (red, green, blue) = (diagonal, cross, c)
            }
            let o = out + 4 * x
            o[0] = UInt8(blue); o[1] = UInt8(green); o[2] = UInt8(red); o[3] = 255
        }
        pixel(0, 1, 1)
        for x in 1..<(w - 1) { pixel(x, x - 1, x + 1) }
        pixel(w - 1, w - 2, w - 2)
    }
}
