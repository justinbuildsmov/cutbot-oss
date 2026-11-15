// faces: where the faces are in a video, so captions can stay off them.
//
//   faces VIDEO [SAMPLES]      prints JSON: {"w":W,"h":H,"frames":N,"boxes":[[x,y,w,h],...]}
//
// Samples SAMPLES frames (default 24) spread over the video, as displayed (rotation
// applied), and runs Vision face detection on each. Boxes are normalized 0-1 with
// the origin at the TOP-left, one per face per sampled frame.
// Build: swiftc -O -o native/bin/faces native/faces.swift
import AVFoundation
import Vision

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: faces VIDEO [SAMPLES]\n".data(using: .utf8)!); exit(2)
}
let samples = args.count > 2 ? max(1, Int(args[2]) ?? 24) : 24
let asset = AVURLAsset(url: URL(fileURLWithPath: args[1]))
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.maximumSize = CGSize(width: 720, height: 720)
gen.requestedTimeToleranceBefore = CMTime(seconds: 0.25, preferredTimescale: 600)
gen.requestedTimeToleranceAfter = CMTime(seconds: 0.25, preferredTimescale: 600)

let dur = CMTimeGetSeconds(asset.duration)
var boxes: [[Double]] = []
var w = 0, h = 0, frames = 0
for i in 0..<samples {
    let t = dur > 0 ? dur * (Double(i) + 0.5) / Double(samples) : 0
    guard let img = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) else { continue }
    w = img.width; h = img.height; frames += 1
    let req = VNDetectFaceRectanglesRequest()
    try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
    for f in req.results ?? [] {
        let b = f.boundingBox  // Vision: origin bottom-left
        let v: [Double] = [Double(b.minX), Double(1 - b.maxY), Double(b.width), Double(b.height)]
        boxes.append(v.map { ($0 * 1000).rounded() / 1000 })
    }
}
let out: [String: Any] = ["w": w, "h": h, "frames": frames, "boxes": boxes]
print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
