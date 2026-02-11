// File: /ui/js/mobile.js
const mobileMatch = window.matchMedia('(max-width: 900px)');

export function isMobileViewport() {
  return mobileMatch.matches || /Android|iPhone|iPad|iPod|IEMobile|BlackBerry|Opera Mini/i.test(navigator.userAgent);
}

export function redirectToMobileIfNeeded(targetPath) {
  if (!isMobileViewport()) return;
  if (!targetPath) return;
  if (window.location.pathname.includes('/mobile/')) return;
  window.location.href = targetPath;
}

export function redirectToDesktopIfNeeded(targetPath) {
  if (isMobileViewport()) return;
  if (!targetPath) return;
  if (!window.location.pathname.includes('/mobile/')) return;
  window.location.href = targetPath;
}
