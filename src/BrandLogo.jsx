export const BRAND_NAME = "旭天 AI VIDEO";

const WORDMARK_SOURCE = "/brand/xutian-wordmark-light.png";
const SYMBOL_SOURCE = "/brand/xutian-symbol-light.png";

export function BrandLogo({
  variant = "wordmark",
  className = "",
  label = BRAND_NAME,
  mobileBreakpoint = 720,
  decorative = false,
}) {
  const classes = ["brand-logo", `brand-logo--${variant}`, className]
    .filter(Boolean)
    .join(" ");

  if (variant === "responsive") {
    return (
      <picture className={classes}>
        <source
          media={`(max-width: ${mobileBreakpoint}px)`}
          srcSet={SYMBOL_SOURCE}
        />
        <img
          className="brand-logo__image"
          src={WORDMARK_SOURCE}
          alt={decorative ? "" : label}
          aria-hidden={decorative ? "true" : undefined}
          decoding="async"
        />
      </picture>
    );
  }

  return (
    <img
      className={classes}
      src={variant === "symbol" ? SYMBOL_SOURCE : WORDMARK_SOURCE}
      alt={decorative ? "" : label}
      aria-hidden={decorative ? "true" : undefined}
      decoding="async"
    />
  );
}
