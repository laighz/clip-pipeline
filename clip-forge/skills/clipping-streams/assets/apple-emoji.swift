// Render the local Apple Color Emoji font into transparent sprites; never substitute another font.
import AppKit
import CoreText

let args = CommandLine.arguments
let output = URL(fileURLWithPath: args[1], isDirectory: true)
let font = CTFontCreateWithName("Apple Color Emoji" as CFString, 168, nil)
guard CTFontCopyPostScriptName(font) as String == "AppleColorEmoji" else {
    fatalError("Apple Color Emoji unavailable")
}
let size = 256
for (index, glyph) in args.dropFirst(2).enumerated() {
    let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                        bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    let line = CTLineCreateWithAttributedString(NSAttributedString(string: glyph,
        attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font]))
    ctx.textMatrix = .identity
    ctx.textPosition = CGPoint(x: 37, y: 65)
    CTLineDraw(line, ctx)
    let image = NSBitmapImageRep(cgImage: ctx.makeImage()!)
    try image.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent("\(index).png"))
}
