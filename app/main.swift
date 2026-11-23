// Claude Tickets — a native window around the Slack bridge's dashboard (http://127.0.0.1:8790).
// Build: ~/.claude/jira-automation/tickets-app/build.sh
import Cocoa
import WebKit

let dashboardURL = URL(string: ProcessInfo.processInfo.environment["CT_URL"] ?? "http://127.0.0.1:8790/")!
let logFile = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/ClaudeTickets.log")

func log(_ msg: String) {
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(msg)\n"
    if let h = try? FileHandle(forWritingTo: logFile) {
        h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); try? h.close()
    } else {
        try? line.write(to: logFile, atomically: true, encoding: .utf8)
    }
}

let offlinePage = """
<!doctype html><meta charset="utf-8"><style>
:root{color-scheme:light dark}body{font:14px -apple-system,sans-serif;display:grid;place-items:center;height:100vh;margin:0;
background:Canvas;color:CanvasText}div{max-width:440px;text-align:center}h1{font-size:17px}p{opacity:.7}
button{font:inherit;padding:7px 14px;border-radius:8px;border:0;background:#c2622d;color:#fff;cursor:pointer}</style>
<div><h1>The Claude bridge is not answering</h1>
<p>The dashboard is served by the Slack bridge on this Mac. It may be restarting, or it stopped.
This window retries every 15 seconds.</p>
<button onclick="window.webkit.messageHandlers.bridge.postMessage('restart');this.textContent='Restarting…'">Restart the bridge</button>
<p><a href="#" onclick="window.webkit.messageHandlers.bridge.postMessage('reload')">Try again</a></p></div>
"""

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var retryTimer: Timer?

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "bridge")
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        web.allowsBackForwardNavigationGestures = false

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Claude Tickets"
        window.minSize = NSSize(width: 420, height: 400)
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("ClaudeTicketsMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        load()
        log("launched, loading \(dashboardURL)")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationShouldHandleReopen(_ app: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        window.makeKeyAndOrderFront(nil); return true
    }

    @objc func load() {
        retryTimer?.invalidate()
        web.load(URLRequest(url: dashboardURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 6))
    }

    func showOffline() {
        web.loadHTMLString(offlinePage, baseURL: nil)
        retryTimer?.invalidate()
        retryTimer = Timer.scheduledTimer(timeInterval: 15, target: self, selector: #selector(load), userInfo: nil, repeats: false)
    }

    // MARK: navigation
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) {
        log("load failed: \(e.localizedDescription)"); if w === web { showOffline() }
    }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) {
        log("not reachable: \(e.localizedDescription)"); if w === web { showOffline() }
    }
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) {
        if w !== web, let win = w.window, let t = w.title, !t.isEmpty { win.title = t }
        guard w === web, w.url?.host == dashboardURL.host else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 12) {
            w.evaluateJavaScript("document.querySelectorAll('.card').length") { r, _ in log("dashboard loaded, cards=\(r ?? "?")") }
        }
    }

    // Links to Jira / GitHub / Slack open in the default browser; only the dashboard stays in the window.
    func webView(_ w: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, host != dashboardURL.host, url.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(url); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }
    var extraWindows: [NSWindow] = []

    // window.open: dashboard pages (review reports from a thread) get their own app window; anything else
    // goes to the default browser.
    func webView(_ w: WKWebView, createWebViewWith c: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures f: WKWindowFeatures) -> WKWebView? {
        guard let url = action.request.url else { return nil }
        if url.host != dashboardURL.host {
            NSWorkspace.shared.open(url); return nil
        }
        let child = WKWebView(frame: .zero, configuration: c)
        child.navigationDelegate = self
        child.uiDelegate = self
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 820),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        win.title = "Review report"
        win.contentView = child
        win.isReleasedWhenClosed = false
        win.cascadeTopLeft(from: NSPoint(x: window.frame.minX + 40, y: window.frame.maxY - 40))
        win.makeKeyAndOrderFront(nil)
        extraWindows.append(win)
        return child
    }

    func webView(_ w: WKWebView, didCommit n: WKNavigation!) {
        if w !== web, let win = w.window { win.title = w.title?.isEmpty == false ? w.title! : "Review report" }
    }

    // MARK: JavaScript dialogs (the dashboard asks before starting or stopping a run)
    func webView(_ w: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }
    func webView(_ w: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame f: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message
        a.beginSheetModal(for: window) { _ in completionHandler() }
    }

    // MARK: messages from the offline page
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard let cmd = m.body as? String else { return }
        if cmd == "restart" {
            // A restart stops every /migrate or /fix run the bridge is doing. Warn first.
            let busy = shell("pgrep -f 'claude -p' >/dev/null && echo busy || echo idle").contains("busy")
            if busy {
                let a = NSAlert()
                a.alertStyle = .warning
                a.messageText = "A Claude run is in progress"
                a.informativeText = "Restarting the bridge stops the /migrate or /fix that is running now. Wait for it to finish, or restart anyway?"
                a.addButton(withTitle: "Wait")
                a.addButton(withTitle: "Restart anyway")
                a.beginSheetModal(for: window) { r in
                    if r == .alertSecondButtonReturn { self.restartBridge() } else { self.load() }
                }
            } else {
                restartBridge()
            }
        } else if cmd == "reload" {
            load()
        }
    }

    func shell(_ command: String) -> String {
        let p = Process(), pipe = Pipe()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", command]
        p.standardOutput = pipe
        try? p.run(); p.waitUntilExit()
        return String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
    }

    func restartBridge() {
        log("restarting the bridge")
        _ = shell("launchctl kickstart -k gui/$(id -u)/local.claude-slack-bridge")
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { self.load() }
    }

    // MARK: menu
    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Claude Tickets", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Claude Tickets", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit Claude Tickets", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "View")
        view.addItem(withTitle: "Reload", action: #selector(load), keyEquivalent: "r").target = self
        viewItem.submenu = view

        let winItem = NSMenuItem(); main.addItem(winItem)
        let win = NSMenu(title: "Window")
        win.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        win.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = win
        NSApp.windowsMenu = win
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
