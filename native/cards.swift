// cards: renders one graphic card (a self-contained scene .html page) to PNG frames.
//
//   cards TEMPLATE.html PARAMS_JSON OUTDIR TIMES
//     TIMES: comma-separated seconds, e.g. 0,0.033,0.067,...  -> OUTDIR/0000.png, 0001.png, ...
//
// The page takes its params as JSON in the URL hash and exposes window.ready (a Promise)
// and window.render(t), drawing into a 760x400 canvas (or 1080x1920 with MODE "full", left unmasked). Each frame is read straight off
// the canvas, then given rounded (continuous) corners and a hairline rim, transparent
// outside, so ffmpeg can lay it over the video as is. Runs headless in WebKit; no network.
// Build: swiftc -O -o native/bin/cards native/cards.swift
import AppKit
import WebKit

let args = CommandLine.arguments
guard args.count == 5 else {
    FileHandle.standardError.write("usage: cards TEMPLATE.html PARAMS_JSON OUTDIR TIMES\n".data(using: .utf8)!); exit(2)
}
let page = URL(fileURLWithPath: args[1]).absoluteURL.standardizedFileURL
let params = args[2]
let outDir = URL(fileURLWithPath: args[3])
// MODE "full" is a full-screen cutaway (1080x1920, opaque): no corner mask or rim.
let fullScreen = ((try? JSONSerialization.jsonObject(with: Data(params.utf8))) as? [String: Any])?["MODE"] as? String == "full"
let times = args[4].split(separator: ",").compactMap { Double($0) }
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!); exit(1)
}

// Corner mask: continuous-looking corners (radius 36 px on 760x400 = 18 logical).
func masked(_ png: Data) -> Data? {
    guard let src = NSBitmapImageRep(data: png), let cg = src.cgImage else { return nil }
    let w = cg.width, h = cg.height
    guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                              space: CGColorSpaceCreateDeviceRGB(),
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
    let rect = CGRect(x: 0, y: 0, width: w, height: h)
    let path = NSBezierPath(roundedRect: rect, xRadius: 36, yRadius: 36).cgPath
    ctx.addPath(path); ctx.clip()
    ctx.draw(cg, in: rect)
    ctx.resetClip()
    ctx.addPath(NSBezierPath(roundedRect: rect.insetBy(dx: 1, dy: 1), xRadius: 35, yRadius: 35).cgPath)
    ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.09)); ctx.setLineWidth(2); ctx.strokePath()
    guard let out = ctx.makeImage() else { return nil }
    return NSBitmapImageRep(cgImage: out).representation(using: .png, properties: [:])
}

extension NSBezierPath {
    var cgPath: CGPath {
        let p = CGMutablePath(); var pts = [NSPoint](repeating: .zero, count: 3)
        for i in 0..<elementCount {
            switch element(at: i, associatedPoints: &pts) {
            case .moveTo: p.move(to: pts[0])
            case .lineTo: p.addLine(to: pts[0])
            case .curveTo, .cubicCurveTo: p.addCurve(to: pts[2], control1: pts[0], control2: pts[1])
            case .quadraticCurveTo: p.addQuadCurve(to: pts[1], control: pts[0])
            case .closePath: p.closeSubpath()
            @unknown default: break
            }
        }
        return p
    }
}

final class Runner: NSObject, WKNavigationDelegate {
    let web: WKWebView
    override init() {
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .nonPersistent()
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: 380, height: 200), configuration: cfg)
        super.init()
        web.navigationDelegate = self
        // Scene code can come from a model reading customer text: no network, ever.
        let rules = "[" + ["^https?://", "^wss?://", "^ftp://"].map {
            #"{"trigger":{"url-filter":""# + $0 + #""},"action":{"type":"block"}}"#
        }.joined(separator: ",") + "]"
        WKContentRuleListStore.default().compileContentRuleList(forIdentifier: "cutbot-offline",
                                                                encodedContentRuleList: rules) { list, err in
            guard let list else { fail("rules failed: \(String(describing: err))") }
            self.web.configuration.userContentController.add(list)
            var c = URLComponents(url: page, resolvingAgainstBaseURL: false)!
            c.fragment = params
            self.web.loadFileURL(c.url!, allowingReadAccessTo: page.deletingLastPathComponent())
        }
    }
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        decisionHandler(a.request.url?.isFileURL == true ? .allow : .cancel)  // no navigating away
    }
    func webView(_ w: WKWebView, didFinish _: WKNavigation!) { Task { await run() } }
    func webView(_ w: WKWebView, didFail _: WKNavigation!, withError e: Error) { fail("load failed: \(e)") }
    func webView(_ w: WKWebView, didFailProvisionalNavigation _: WKNavigation!, withError e: Error) { fail("load failed: \(e)") }

    @MainActor func run() async {
        do {
            _ = try await web.callAsyncJavaScript("await window.ready; return 1", contentWorld: .page)
            for (i, t) in times.enumerated() {
                let r = try await web.callAsyncJavaScript(
                    "window.render(t); return [document.querySelector('canvas').toDataURL('image/png'), " +
                    "JSON.stringify(window.__text || null)]",
                    arguments: ["t": t], contentWorld: .page)
                guard let pair = r as? [Any], let s = pair.first as? String, let comma = s.firstIndex(of: ","),
                      let png = Data(base64Encoded: String(s[s.index(after: comma)...])),
                      let out = fullScreen ? png : masked(png) else { fail("frame \(i) failed") }
                try out.write(to: outDir.appendingPathComponent(String(format: "%04d.png", i)))
                // the runtime's text audit (smallest text drawn, text outside SAFE) beside each frame
                if let audit = pair.last as? String, audit != "null" {
                    try audit.write(to: outDir.appendingPathComponent(String(format: "%04d.json", i)), atomically: true, encoding: .utf8)
                }
            }
            exit(0)
        } catch { fail("render failed: \(error)") }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let runner = Runner()
DispatchQueue.main.asyncAfter(deadline: .now() + 180) { fail("timed out") }
app.run()
