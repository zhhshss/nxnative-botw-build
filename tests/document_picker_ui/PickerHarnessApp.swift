// Current production launcher, without game code or GPU rendering.
import UIKit

final class PickerHarnessRoot: UIViewController {
    private var shown = false
    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .landscape }
    override var prefersStatusBarHidden: Bool { true }
    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        if !shown {
            shown = true
            _ = dmShowLauncher(Unmanaged.passUnretained(self).toOpaque(), {})
        }
    }
}

final class PickerHarnessScene: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?
    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let fm = FileManager.default
        let doc = fm.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let resources = doc.appendingPathComponent("Picker Resources", isDirectory: true)
        try? fm.createDirectory(at: resources, withIntermediateDirectories: true)
        if let fixtures = Bundle.main.resourceURL?.appendingPathComponent("picker-fixtures"),
           let files = try? fm.contentsOfDirectory(at: fixtures, includingPropertiesForKeys: nil) {
            for file in files {
                let target = resources.appendingPathComponent(file.lastPathComponent)
                if !fm.fileExists(atPath: target.path) { try? fm.copyItem(at: file, to: target) }
            }
        }
        try? fm.createDirectory(at: doc.appendingPathComponent("Picker Saves", isDirectory: true), withIntermediateDirectories: true)
        let window = UIWindow(windowScene: scene)
        window.rootViewController = PickerHarnessRoot()
        window.backgroundColor = .black
        window.makeKeyAndVisible()
        self.window = window
    }
}

@main final class PickerHarnessApp: UIResponder, UIApplicationDelegate {
    func application(_ application: UIApplication, configurationForConnecting session: UISceneSession,
                     options: UIScene.ConnectionOptions) -> UISceneConfiguration {
        let config = UISceneConfiguration(name: "Picker test", sessionRole: session.role)
        config.delegateClass = PickerHarnessScene.self
        return config
    }
}
