// captions: draws CutBot's burned-in captions as transparent PNGs, one per chunk.
// (Homebrew ffmpeg often ships without drawtext, so text is drawn here and overlaid.)
//
//   captions W H OUTDIR [Y] < chunks.json    chunks.json: ["two to four", "words each", ...]
//   Y: where the middle of the line goes, 0-1 from the top (cutbot picks it off the face).
//
// Writes OUTDIR/0000.png, 0001.png, ... at W x H. Style: heavy rounded sans, white
// with a black outline, centred a little below the middle of the frame, clear of
// the top ~11% and bottom ~28% that phone apps cover.
// Build: swiftc -O -o native/bin/captions native/captions.swift
import AppKit

let args = CommandLine.arguments
guard args.count == 4 || args.count == 5, let W = Int(args[1]), let H = Int(args[2]) else {
    FileHandle.standardError.write("usage: captions W H OUTDIR [Y] < chunks.json\n".data(using: .utf8)!)
    exit(2)
}
let outDir = URL(fileURLWithPath: args[3])
let chunks = try! JSONDecoder().decode([String].self, from: FileHandle.standardInput.readDataToEndOfFile())

let short = CGFloat(min(W, H))
var size = short * 0.075
let base = NSFont.systemFont(ofSize: size, weight: .heavy)
func font(_ s: CGFloat) -> NSFont {
    let d = base.fontDescriptor.withDesign(.rounded) ?? base.fontDescriptor
    return NSFont(descriptor: d, size: s) ?? NSFont.systemFont(ofSize: s, weight: .heavy)
}
let maxWidth = CGFloat(W) * 0.84
let yTop = args.count == 5 ? Double(args[4]) : nil   // 0-1 from the top
let centerY = yTop.map { CGFloat(H) * CGFloat(1 - $0) } ?? CGFloat(H) * (W < H ? 0.36 : 0.30)   // AppKit counts from the bottom

for (i, text) in chunks.enumerated() {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: W, pixelsHigh: H, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    var s = size
    var f = font(s)
    let style = NSMutableParagraphStyle(); style.alignment = .center
    func attrs(_ stroke: Bool) -> [NSAttributedString.Key: Any] {
        var a: [NSAttributedString.Key: Any] = [.font: f, .paragraphStyle: style,
                                                .foregroundColor: NSColor.white]
        if stroke { a[.strokeColor] = NSColor.black; a[.strokeWidth] = 22.0 }  // % of size, outside+inside
        return a
    }
    // shrink long chunks to fit one line
    while NSAttributedString(string: text, attributes: attrs(false)).size().width > maxWidth && s > short * 0.04 {
        s -= 2; f = font(s)
    }
    let h = NSAttributedString(string: text, attributes: attrs(false)).size().height
    let rect = NSRect(x: (CGFloat(W) - maxWidth) / 2, y: centerY - h / 2, width: maxWidth, height: h)
    NSAttributedString(string: text, attributes: attrs(true)).draw(in: rect)   // outline
    NSAttributedString(string: text, attributes: attrs(false)).draw(in: rect)  // fill on top
    NSGraphicsContext.restoreGraphicsState()
    let png = rep.representation(using: .png, properties: [:])!
    try! png.write(to: outDir.appendingPathComponent(String(format: "%04d.png", i)))
}
