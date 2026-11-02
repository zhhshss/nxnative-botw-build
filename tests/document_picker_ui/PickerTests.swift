import XCTest

final class PickerTests: XCTestCase {
    func testLauncherResourceAndCloudPickers() throws {
        continueAfterFailure = false
        let app = XCUIApplication(bundleIdentifier: "org.nxnative.botwlaunchertest")
        app.launchArguments = ["--require-rom", "-AppleLanguages", "(en)", "-AppleLocale", "en_US"]
        XCUIDevice.shared.orientation = .landscapeLeft
        app.launch()
        let rom = app.buttons["startup.import.rom"]
        XCTAssertTrue(rom.waitForExistence(timeout: 15), app.debugDescription)
        rom.tap()
        sleep(3)
        attach(app, name: "ROM picker")
        dismissPicker(app)
        let shaders = app.buttons["startup.import.shaders"]
        XCTAssertTrue(shaders.waitForExistence(timeout: 5), app.debugDescription)
        shaders.tap()
        sleep(3)
        attach(app, name: "Graphics picker")
        dismissPicker(app)
        let settings = app.buttons["launcher.settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 5), app.debugDescription)
        settings.tap()
        let cloud = app.buttons["settings.section.Cloud saves"]
        XCTAssertTrue(cloud.waitForExistence(timeout: 5), app.debugDescription)
        cloud.tap()
        let folder = app.buttons["Choose a folder in iCloud Drive…"]
        XCTAssertTrue(folder.waitForExistence(timeout: 5), app.debugDescription)
        folder.tap()
        sleep(3)
        attach(app, name: "Cloud folder picker")
        dismissPicker(app)
    }

    private func attach(_ app: XCUIApplication, name: String) {
        let text = XCTAttachment(string: app.debugDescription)
        text.name = name + " accessibility"
        text.lifetime = .keepAlways
        add(text)
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
        print("PICKER TREE " + name + "\n" + app.debugDescription)
    }

    private func dismissPicker(_ app: XCUIApplication) {
        let cancel = app.buttons["Cancel"].firstMatch
        let close = app.buttons["Close"].firstMatch
        if cancel.exists { cancel.tap() }
        else if close.exists { close.tap() }
        else { app.coordinate(withNormalizedOffset: CGVector(dx: 0.125, dy: 0.135)).tap() }
        sleep(1)
    }
}
