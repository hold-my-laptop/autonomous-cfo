/**
 * Fades out and removes the loading screen from index.html once the app has its first data,
 * and not before the intro animation has played (about 1.4 s after the page started loading).
 */
export function dismissBootSplash() {
  const el = document.getElementById("boot-splash");
  if (!el || el.dataset.leaving) return;
  el.dataset.leaving = "1";
  const wait = Math.max(0, 1400 - performance.now());
  window.setTimeout(() => {
    el.classList.add("boot-splash-out");
    window.setTimeout(() => el.remove(), 550);
  }, wait);
}
