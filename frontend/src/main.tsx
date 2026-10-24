import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { get, formatTime, type FramePacket, type StreamInfo } from './api';
import './style.css';
import { Review } from './Review';

function App() {
  const [sources, setSources] = useState<StreamInfo[]>([]);
  const [source, setSource] = useState('synthetic');
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [packet, setPacket] = useState<FramePacket | null>(null);
  const [error, setError] = useState('');
  const info = sources.find(s => s.source_id === source);
  useEffect(() => {get<StreamInfo[]>('sources').then(setSources).catch(e => setError(String(e)));}, []);
  useEffect(() => {
    let active = true;
    setPacket(null);
    get<FramePacket>(`preview/${source}/${index}`).then(p => {if (active) {setPacket(p); setError('');}})
      .catch(e => {if (active) {setError(String(e)); setPlaying(false);}});
    return () => {active = false;};
  }, [source, index]);
  // Wait for the displayed frame metadata before advancing; preview is not a capture clock.
  useEffect(() => {
    if (!playing || !packet) return;
    const id = window.setTimeout(() => setIndex(i => (i + 1) % (info?.frame_count ?? 120)), 1000 / (info?.fps ?? 20));
    return () => window.clearTimeout(id);
  }, [playing, packet, info]);
  return <main>
    <header><span className="eyebrow">STATIONARY SKY OBSERVATION / OFFLINE BETA</span><h1>UAP Observer</h1><p>Offline lab <span className="badge">Generated footage</span></p></header>
    <section className="notice">Software test only. No camera connected. Generated detections remain unresolved; this is not field-performance validation.</section>
    <section className="panel">
      <label>Preview source <select value={source} onChange={e => {setPlaying(false); setSource(e.target.value); setIndex(0);}}>
        {sources.map(s => <option key={s.source_id} value={s.source_id}>{s.source_id === 'synthetic' ? 'Synthetic generator' : 'Encoded video-file replay'}</option>)}
      </select></label>
      <div className="sky">{packet && <img key={`${source}-${index}`} src={`/api/v1/preview/${source}/${index}/image`} alt="Generated sky with moving and blinking points" onError={() => {setError('Preview image failed to load'); setPlaying(false);}}/>}</div>
      <div className="controls"><button onClick={() => setPlaying(p => !p)} disabled={!!error}>{playing ? 'Pause' : 'Play'}</button><button onClick={() => {setPlaying(false); setIndex(0);}}>Restart</button>
        <label className="scrub">Frame <input aria-label="Frame" type="range" min="0" max={(info?.frame_count ?? 120) - 1} value={index} onChange={e => {setPlaying(false); setIndex(Number(e.target.value));}}/></label>
      </div>
      {error && <p role="alert">{error}</p>}
      <dl><div><dt>Media timestamp</dt><dd data-testid="timestamp">{packet ? formatTime(packet.relative_seconds) : 'Loading…'}</dd></div><div><dt>Source frame</dt><dd>{packet?.source_frame_index ?? '—'}</dd></div><div><dt>Source PTS / time base</dt><dd>{packet?.source_pts ?? '—'} / {packet?.time_base ?? '—'}</dd></div><div><dt>Decoded format</dt><dd>{info ? `${info.width} × ${info.height} · ${info.fps} fps · ${info.codec}` : '—'}</dd></div></dl>
      <p className="muted">Timestamp basis: {packet?.timestamp_basis ?? '—'}. Media time is not a UTC exposure measurement. Playback speed depends on the browser.</p>
    </section>
    <Review/>
    <footer>Two scripted targets, blinking, seeded noise, stars, jitter and exposure change. Ground truth is generated, not inferred. <a href="/docs">API documentation</a></footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
