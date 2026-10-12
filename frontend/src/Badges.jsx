// Camelot codes get a hue from their wheel position so compatible keys
// (neighbours, same number) look alike at a glance.
export function KeyBadge({ track }) {
  if (!track?.camelot) return <span className="badge muted">—</span>;
  const num = parseInt(track.camelot, 10);
  const minor = track.camelot.endsWith("A");
  const hue = ((num - 1) * 30 + 200) % 360;
  return (
    <span
      className={`badge key ${minor ? "minor" : ""}`}
      style={{ "--badge-hue": hue }}
      title={`${track.key_name} (${minor ? "minor" : "major"}), confidence ${track.key_confidence ?? "?"}`}
    >
      {track.camelot} <small>{track.key_name}</small>
    </span>
  );
}

export function BpmBadge({ track }) {
  if (!track?.bpm) return <span className="badge muted">—</span>;
  return <span className="badge bpm">{track.bpm.toFixed(1)} BPM</span>;
}

export function ProgressBar({ value, status }) {
  return (
    <div className={`progress ${status ?? ""}`}>
      <div className="progress-fill" style={{ width: `${Math.round((value ?? 0) * 100)}%` }} />
    </div>
  );
}
