/**
 * The Vero logo mark (the "V"), traced from the brand artwork.
 * Uses currentColor, so it is navy on light surfaces and white in the dark theme.
 * The same paths are inlined in index.html (loading screen) and public/favicon.svg.
 */
export function LogoMark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 3 372 281" fill="currentColor" aria-hidden="true" className={className}>
      <path d="M2 4C70 3 128 26 162 78L242 206L196 282L52 50C36 26 18 8 2 4Z" />
      <path d="M286 4.5H368L255 187L214 121Z" />
    </svg>
  );
}

/** "Vero" wordmark: the logo mark stands in for the V. Screen readers hear "Vero". */
export function Wordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-baseline whitespace-nowrap font-semibold tracking-[-0.02em] ${className}`}>
      <LogoMark className="mr-[0.07em] h-[0.84em] w-auto translate-y-[0.05em] self-baseline" />
      <span aria-hidden="true">ero</span>
      <span className="sr-only">Vero</span>
    </span>
  );
}

