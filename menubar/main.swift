// AgentSecrets Manager — menu bar app.
// Starts the bundled Python server (agentsecrets_gui.py) and shows its UI in a popover.
import AppKit
import WebKit

// The server writes its URL, with the per-run token in the fragment, to this 0600 file.
let urlFile = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/AgentSecretsManager/url")

final class AppDelegate: NSObject, NSApplicationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var statusItem: NSStatusItem!
    let popover = NSPopover()
    var webView: WKWebView!
    var server: Process?
    var appURL: URL?

    func applicationDidFinishLaunching(_ note: Notification) {
        installEditMenu()
        startServer()

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "key.fill", accessibilityDescription: "AgentSecrets")
            button.action = #selector(clicked(_:))
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "native")
        webView = WKWebView(frame: NSRect(x: 0, y: 0, width: 880, height: 600), configuration: config)
        webView.uiDelegate = self
        let vc = NSViewController()
        vc.view = webView
        popover.contentViewController = vc
        popover.contentSize = NSSize(width: 880, height: 600)
        popover.behavior = .transient
        loadWhenReady(attempts: 40)
    }

    func applicationWillTerminate(_ note: Notification) {
        server?.terminate()
    }

    // MARK: server

    func startServer() {
        guard let script = Bundle.main.path(forResource: "agentsecrets_gui", ofType: "py") else { return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [script, "--no-browser"]
        var env = ProcessInfo.processInfo.environment
        // Apps launched from Finder get a minimal PATH; make Homebrew's agentsecrets findable.
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        env["AGENTSECRETS_GUI_EMBEDDED"] = "1"
        p.environment = env
        let logURL = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/AgentSecretsManager.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let log = try? FileHandle(forWritingTo: logURL) {
            log.seekToEndOfFile()
            p.standardOutput = log
            p.standardError = log
        }
        try? p.run()
        server = p
    }

    // The server takes a moment to start; retry until it answers to the token in its url file
    // (a file left over from an earlier run has a stale token and gets 403).
    func loadWhenReady(attempts: Int) {
        guard let text = try? String(contentsOf: urlFile, encoding: .utf8),
              let url = URL(string: text.trimmingCharacters(in: .whitespacesAndNewlines)),
              let fragment = url.fragment, fragment.hasPrefix("t="),
              let probe = URL(string: "/api/snapshot", relativeTo: url) else {
            return retryLoad(attempts: attempts)
        }
        var request = URLRequest(url: probe)
        request.setValue(String(fragment.dropFirst(2)), forHTTPHeaderField: "X-Token")
        URLSession.shared.dataTask(with: request) { _, response, _ in
            DispatchQueue.main.async {
                if (response as? HTTPURLResponse)?.statusCode == 200 {
                    self.appURL = url
                    self.webView.load(URLRequest(url: url))
                } else {
                    self.retryLoad(attempts: attempts)
                }
            }
        }.resume()
    }

    func retryLoad(attempts: Int) {
        if attempts > 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self.loadWhenReady(attempts: attempts - 1) }
        } else {
            webView.loadHTMLString(
                "<body style='font:14px -apple-system;padding:40px;text-align:center'>Couldn't start the AgentSecrets server.<br>See ~/Library/Logs/AgentSecretsManager.log</body>",
                baseURL: nil)
        }
    }

    // MARK: status item

    @objc func clicked(_ sender: NSStatusBarButton) {
        if NSApp.currentEvent?.type == .rightMouseUp {
            let menu = NSMenu()
            menu.addItem(withTitle: "Open in Browser", action: #selector(openInBrowser), keyEquivalent: "").target = self
            menu.addItem(withTitle: "Reload", action: #selector(reload), keyEquivalent: "r").target = self
            menu.addItem(.separator())
            menu.addItem(withTitle: "Quit AgentSecrets Manager", action: #selector(quit), keyEquivalent: "q").target = self
            statusItem.menu = menu
            statusItem.button?.performClick(nil)
            statusItem.menu = nil
            return
        }
        if popover.isShown {
            popover.performClose(sender)
        } else {
            NSApp.activate(ignoringOtherApps: true)
            popover.show(relativeTo: sender.bounds, of: sender, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    @objc func openInBrowser() { if let url = appURL { NSWorkspace.shared.open(url) } }
    @objc func reload() { if appURL == nil { loadWhenReady(attempts: 40) } else { webView.reload() } }
    @objc func quit() { NSApp.terminate(nil) }

    // MARK: web view bridges

    // Messages from the page: {"action": "copy", "text": "..."} or {"action": "quit"}.
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let body = message.body as? [String: Any], let action = body["action"] as? String else { return }
        switch action {
        case "copy":
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(body["text"] as? String ?? "", forType: .string)
        case "quit":
            quit()
        default:
            break
        }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    // Accessory apps have no menu bar, so Cmd+C/V/X/A/Z need an Edit menu to work.
    func installEditMenu() {
        let main = NSMenu()
        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
