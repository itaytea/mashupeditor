import { useState } from "react";

/**
 * A skinned window. The default header is the slate title bar with rails;
 * variant="tab" is the slim equalizer-style header with a folder-tab label.
 */
export default function Window({ title, className = "", variant = "bar", onClose, children, footer }) {
  const [shaded, setShaded] = useState(false);
  const buttons = (
    <span className="wa-title-buttons">
      <button
        type="button"
        className="wa-tbtn wa-tbtn-shade"
        aria-label={shaded ? `Expand ${title}` : `Shade ${title}`}
        aria-pressed={shaded}
        onClick={() => setShaded((s) => !s)}
      />
      {onClose && (
        <button type="button" className="wa-tbtn wa-tbtn-close" aria-label={`Close ${title}`} onClick={onClose} />
      )}
    </span>
  );

  return (
    <section className={`wa-window ${className} ${shaded ? "is-shaded" : ""}`} aria-label={title}>
      {variant === "tab" ? (
        <div className="wa-tabbar" onDoubleClick={() => setShaded((s) => !s)}>
          <h2 className="wa-tab-label">{title}</h2>
          {buttons}
        </div>
      ) : (
        <div className="wa-title" onDoubleClick={() => setShaded((s) => !s)}>
          <span className="wa-title-menu" aria-hidden="true" />
          <span className="wa-rail" aria-hidden="true" />
          <h2 className="wa-title-label">{title}</h2>
          <span className="wa-rail" aria-hidden="true" />
          {buttons}
        </div>
      )}
      {!shaded && (
        <>
          <div className="wa-body">{children}</div>
          {footer && <div className="wa-footer">{footer}</div>}
        </>
      )}
    </section>
  );
}
