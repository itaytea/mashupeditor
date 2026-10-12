import { useState } from "react";

/** A skinned window: slate title bar with rails, label, shade/close buttons. */
export default function Window({ title, className = "", onClose, children, footer }) {
  const [shaded, setShaded] = useState(false);
  return (
    <section className={`wa-window ${className} ${shaded ? "is-shaded" : ""}`} aria-label={title}>
      <div className="wa-title" onDoubleClick={() => setShaded((s) => !s)}>
        <span className="wa-title-menu" aria-hidden="true" />
        <span className="wa-rail" aria-hidden="true" />
        <h2 className="wa-title-label">{title}</h2>
        <span className="wa-rail" aria-hidden="true" />
        <span className="wa-title-buttons">
          <button
            type="button"
            className="wa-tbtn wa-tbtn-shade"
            aria-label={shaded ? `Expand ${title}` : `Shade ${title}`}
            aria-pressed={shaded}
            onClick={() => setShaded((s) => !s)}
          />
          {onClose && (
            <button
              type="button"
              className="wa-tbtn wa-tbtn-close"
              aria-label={`Close ${title}`}
              onClick={onClose}
            />
          )}
        </span>
      </div>
      {!shaded && (
        <>
          <div className="wa-body">{children}</div>
          {footer && <div className="wa-footer">{footer}</div>}
        </>
      )}
    </section>
  );
}
