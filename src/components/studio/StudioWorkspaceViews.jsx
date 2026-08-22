import {
  CornersOut,
  Pause,
  Play,
  Plus,
  SpeakerHigh,
} from "@phosphor-icons/react";

function formatTime(seconds) {
  const value = Math.max(0, Math.round(seconds));
  return `00:${String(value).padStart(2, "0")}`;
}

export function IconButton({ label, children, className = "", ...props }) {
  return (
    <button
      className={`icon-button ${className}`}
      type="button"
      aria-label={label}
      title={label}
      {...props}
    >
      {children}
    </button>
  );
}

export function SceneTimeline({ scenes, activeId, onSelect, onAdd }) {
  return (
    <section className="timeline" aria-label="视频镜头">
      {scenes.map((scene) => (
        <button
          className={`scene-card ${activeId === scene.id ? "is-active" : ""}`}
          type="button"
          key={scene.id}
          onClick={() => onSelect(scene.id)}
          aria-pressed={activeId === scene.id}
        >
          <span className="scene-image">
            <img src={scene.image} alt={`${scene.title}预览`} />
            <span className="scene-number">{scene.number}</span>
          </span>
          <span className="scene-meta">
            <span>{scene.range}</span>
            <strong>{scene.title}</strong>
          </span>
        </button>
      ))}
      <button className="add-scene" type="button" onClick={onAdd}>
        <Plus size={26} aria-hidden="true" />
        <span>添加镜头</span>
      </button>
    </section>
  );
}

export function Preview({
  scene,
  duration,
  playing,
  onTogglePlay,
  playhead,
  onSeek,
}) {
  return (
    <section className="preview-shell" aria-label="视频预览">
      <img src={scene.image} alt={`${scene.title}视频画面`} />
      <div className="preview-controls">
        <IconButton label={playing ? "暂停预览" : "播放预览"} onClick={onTogglePlay}>
          {playing ? (
            <Pause size={21} weight="fill" aria-hidden="true" />
          ) : (
            <Play size={21} weight="fill" aria-hidden="true" />
          )}
        </IconButton>
        <span className="timecode">
          {formatTime(playhead)} / {formatTime(duration)}
        </span>
        <input
          className="seek"
          type="range"
          min="0"
          max={duration}
          value={playhead}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-label="预览进度"
        />
        <span className="aspect-label">16:9</span>
        <IconButton label="音量">
          <SpeakerHigh size={21} aria-hidden="true" />
        </IconButton>
        <IconButton label="全屏预览">
          <CornersOut size={21} aria-hidden="true" />
        </IconButton>
      </div>
    </section>
  );
}
