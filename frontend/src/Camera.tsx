import { useEffect, useState, type FormEvent } from 'react';
import { get } from './api';

type CameraStatus = {
  state: string; connection: string; elapsed_seconds?: number;
  packets?: number; session_count?: number; reason?: string;
  preview_receipt_utc?: string; last_packet_age_seconds?: number;
  result?: {frames:number; observed_fps:number|null; streams:{codec:string;width:number;height:number;fps:string}[];
    errors:string[]; gaps:unknown[]; independent_export:{verification:{verified:boolean}}|null};
};

export function Camera() {
  const [status,setStatus] = useState<CameraStatus>({state:'ready',connection:'stopped'});
  const [error,setError] = useState('');
  const [submitting,setSubmitting] = useState(false);
  const [tick,setTick] = useState(0);
  const [showUrl,setShowUrl] = useState(false);
  const [showPassword,setShowPassword] = useState(false);
  useEffect(()=>{
    let active=true;
    const refresh=()=>get<CameraStatus>('camera/status').then(s=>{if(active){setStatus(s);setTick(t=>t+1);}}).catch(()=>{if(active)setError('Local camera status unavailable');});
    void refresh();const timer=window.setInterval(()=>void refresh(),1000);
    return()=>{active=false;window.clearInterval(timer);};
  },[]);
  const active=['capturing','processing'].includes(status.state);
  async function start(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();setSubmitting(true);setError('');
    const form=event.currentTarget;
    const values=new FormData(form);
    // Credentials stay out of React state, URLs, browser storage and application logs.
    const body=JSON.stringify({url:values.get('url'),username:values.get('username'),password:values.get('password')});
    values.delete('url');values.delete('username');values.delete('password');
    setShowUrl(false);setShowPassword(false);
    try {
      const response=await fetch('/api/v1/camera/start',{method:'POST',headers:{'Content-Type':'application/json'},body});
      if(!response.ok)throw Error('Connection could not start. Check the LAN address, login fields, active jobs and free storage.');
      form.reset();
      setStatus({state:'capturing',connection:'connecting'});
    } catch(e) {setError(String(e));} finally {setSubmitting(false);}
  }
  return <section className="panel" aria-label="G100 commissioning">
    <span className="eyebrow">LOCAL CAMERA SETUP</span><h2>Connect your G100</h2>
    <p>Keep the camera paired to Apple Home. In Aqara Home, open the camera’s RTSP LAN Preview settings and use the exact address and login shown there. This Mac must be on the camera’s local network.</p>
    <p>Enter the details here on this Mac. The run keeps them in memory and clears the fields when submitted. Camera access ends after ten minutes; indexing and evidence verification may finish afterward. Audio is off.</p>
    {!active&&<form className="credential-form" autoComplete="off" onSubmit={event=>void start(event)}>
      <div className="credential-field"><label htmlFor="camera-rtsp-url">RTSP address</label><div className="credential-input">
        <input id="camera-rtsp-url" name="url" type={showUrl?'text':'password'} required autoComplete="off" spellCheck={false} maxLength={4096}/>
        <button type="button" aria-label={showUrl?'Hide RTSP address':'Show RTSP address'} aria-controls="camera-rtsp-url" aria-pressed={showUrl} onClick={()=>setShowUrl(v=>!v)}>{showUrl?'Hide':'Show'}</button>
      </div></div>
      <label className="credential-field">Username (if separate)<input name="username" type="text" autoComplete="off" spellCheck={false} maxLength={512}/></label>
      <div className="credential-field"><label htmlFor="camera-password">Password (if separate)</label><div className="credential-input">
        <input id="camera-password" name="password" type={showPassword?'text':'password'} autoComplete="new-password" maxLength={512}/>
        <button type="button" aria-label={showPassword?'Hide password':'Show password'} aria-controls="camera-password" aria-pressed={showPassword} onClick={()=>setShowPassword(v=>!v)}>{showPassword?'Hide':'Show'}</button>
      </div></div>
      <p className="muted">The RTSP address may contain a password; use Show to check it. Use the camera’s numeric LAN address. If the copied RTSP address already includes the login, leave the separate fields empty. No saved password or automatic restart is configured.</p>
      <button type="submit" disabled={submitting}>{submitting?'Starting…':'Start ten-minute camera test'}</button>
    </form>}
    <p role="status">Camera test: <strong>{status.state}</strong> · connection: {status.connection}
      {status.elapsed_seconds!==undefined&&status.elapsed_seconds!==null&&` · ${status.elapsed_seconds.toFixed(0)} seconds`}
      {status.packets!==undefined&&` · ${status.packets} packets · ${status.session_count} sessions`}</p>
    {active&&<button onClick={()=>void fetch('/api/v1/camera/stop',{method:'POST'}).then(r=>{if(!r.ok)throw Error('stop failed');}).catch(()=>setError('Stop request failed; stop the local server with Ctrl-C.'))}>Stop camera test</button>}
    {status.reason&&<p className="notice">{status.reason}</p>}
    {error&&<p role="alert">{error}</p>}
    {status.preview_receipt_utc&&<><img style={{width:'100%'}} src={`/api/v1/camera/preview.jpg?t=${tick}`} alt="Delayed camera preview from archived source"/><p className="muted">Delayed derivative preview · source packet received {status.preview_receipt_utc}. This is not an exposure UTC measurement. Last packet age: {status.last_packet_age_seconds??'—'} s.</p></>}
    {status.result&&<div><p>{status.result.frames} indexed frames · {status.result.observed_fps??'—'} observed fps · {status.result.errors.length} capture errors · {status.result.gaps.length} gap records.</p>
      {status.result.streams.map((s,i)=><p key={i}>{s.width??'—'} × {s.height??'—'} · {s.codec??'unavailable'} · reported {s.fps??'—'} fps</p>)}
      <p>{status.result.independent_export?.verification.verified?'An event export passed verification.':'No verified event export yet. Review the event list below.'}</p>
      <p>Confirm the ordinary movement in the event viewer and check Apple Home still plays the camera. A smoke test does not establish field detection accuracy or long-term reliability.</p></div>}
  </section>;
}
