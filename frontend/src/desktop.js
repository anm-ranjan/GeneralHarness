/** True when the UI is running inside the Electron shell, which stamps
 *  X-MyHarness-Desktop on backend requests and exposes window.myharnessDesktop.
 *  Desktop-only features (file editing) must gate on this. */
export function isDesktopApp() {
  return typeof window !== 'undefined' && !!window.myharnessDesktop
}
