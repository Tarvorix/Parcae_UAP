import { useCallback, useEffect, useState } from 'react';
import { get } from './api';

type EventRow = {id:string;state:string;start_seconds:number;end_seconds:number;created_utc:string};
type Point = {x_px:number;y_px:number;time_seconds:number;frame_id?:string};
type Track = {observations:Point[];fitted_path:Point[]};
type Analysis = {id:string;data:{class:string;reason_codes:string[];track?:Track;tracks?:Track[]}};
type Detail = {event_id:string;state:string;start_seconds:number;end_seconds:number;capture_manifest_sha256:string;
  capture:{complete:boolean;limitations:string[];stream_info:{width:number;height:number}} | null;
  segments:{id:string;start_seconds:number;end_seconds:number;sha256:string;bytes:number}[];
  analyses:Analysis[];reviews:{id:number;assessment:string;note:string}[];calibration:{state:string;reason:string}};
type Settings = {revision:number;detector:Record<string,unknown>;rectangles:number[][]};
async function mutate<T>(path:string,method:string,body:unknown={}):Promise<T> {
  const response=await fetch(`/api/v1/${path}`,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok) throw new Error(`Request failed (${response.status}). Check source integrity or local service.`);
  return response.json() as Promise<T>;
}

export function Review() {
  const [events,setEvents]=useState<EventRow[]>([]);
  const [page,setPage]=useState(1);
  const [search,setSearch]=useState('');
  const [detail,setDetail]=useState<Detail|null>(null);
  const [job,setJob]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [segment,setSegment]=useState('');
  const [playTime,setPlayTime]=useState(0);
  const [overlay,setOverlay]=useState(true);
  const [assessment,setAssessment]=useState('unresolved_insufficient_data');
  const [note,setNote]=useState('');
  const [download,setDownload]=useState('');
  const [settings,setSettings]=useState<Settings|null>(null);
  const [mask,setMask]=useState('');
  const [connection,setConnection]=useState('Connecting');
  const [stats,setStats]=useState<{disk_free_bytes:number;application_bytes:number;active_job:boolean}|null>(null);
  const refresh=useCallback(async()=>{setEvents(await get<EventRow[]>(`events?page=${page}&q=${encodeURIComponent(search)}`));setStats(await get('stats'));},[page,search]);
  useEffect(()=>{void refresh().catch(e=>setError(String(e)));},[refresh]);
  useEffect(()=>{get<Settings>('settings').then(setSettings).catch(e=>setError(String(e)));},[]);
  useEffect(()=>{
    let stopped=false;let socket:WebSocket|null=null;let retry=0;let timer=0;let previousCount=-1;
    const connect=()=>{socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/v1/ws/updates`);
      socket.onopen=()=>{setConnection('Connected');retry=0;};
      socket.onmessage=e=>{const value=JSON.parse(e.data) as {event_count:number};if(value.event_count!==previousCount){previousCount=value.event_count;void refresh().catch(e=>setError(String(e)));}};
      socket.onclose=()=>{if(!stopped){setConnection('Reconnecting');timer=window.setTimeout(connect,Math.min(5000,1000*2**retry++));}};
    };connect();return()=>{stopped=true;window.clearTimeout(timer);socket?.close();};
  },[refresh]);
  useEffect(()=>{
    if(!job)return;
    let active=true;
    const id=window.setInterval(()=>{get<{state:string;error:string|null}>(`jobs/${job}`).then(async result=>{
      if(!active)return;
      if(result.state!=='running'){setBusy(false);setJob('');await refresh();if(result.state!=='complete')setError(`Processing ${result.state}: ${result.error}`);}
    }).catch(e=>{if(active){setError(String(e));setBusy(false);}});},500);
    return()=>{active=false;window.clearInterval(id);};
  },[job,refresh]);
  async function open(id:string){const value=await get<Detail>(`events/${id}`);setDetail(value);setSegment(value.segments[0]?.id??'');setDownload('');setPlayTime(0);}
  async function start(){try{setError('');setBusy(true);const result=await mutate<{job_id:string}>('demo/start','POST');setJob(result.job_id);}catch(e){setError(String(e));setBusy(false);}}
  const selected=detail?.segments.find(s=>s.id===segment);
  const tracks=detail?.analyses.flatMap(a=>a.data.track?[a.data.track]:a.data.tracks??[])??[];
  const time=(selected?.start_seconds??0)+playTime;
  const width=detail?.capture?.stream_info.width??320,height=detail?.capture?.stream_info.height??180;
  return <section className="review-section">
    <div className="section-heading"><div><span className="eyebrow">OFFLINE EVIDENCE WORKFLOW</span><h2>Observe, inspect, review</h2></div><button onClick={()=>void start()} disabled={busy}>{busy?'Processing generated footage…':'Analyze generated footage'}</button></div>
    <p className="muted">Native 320 × 180 / 20 fps analysis. Generated detections are software tests. Physical speed, range and sky calibration are unavailable.</p>
    {stats&&<p className="muted">Local data {(stats.application_bytes/1048576).toFixed(1)} MiB · free disk {(stats.disk_free_bytes/1073741824).toFixed(1)} GiB · no real camera connected</p>}
    <p className="muted">Local updates: <span data-testid="connection">{connection}</span></p>
    {error&&<p role="alert">{error}</p>}
    <div className="event-grid"><aside className="panel event-list"><h3>Recent events</h3><label>Find event<input aria-label="Find event" value={search} placeholder="Event ID / UTC date" onChange={e=>{setSearch(e.target.value);setPage(1);}}/></label><button onClick={()=>void refresh().catch(e=>setError(String(e)))}>Refresh</button>
      {!events.length&&<p>No events yet. Analyze the generated footage to begin.</p>}
      {events.map(event=><button className={`event-row ${detail?.event_id===event.id?'selected':''}`} key={event.id} onClick={()=>void open(event.id).catch(e=>setError(String(e)))}>
        <strong>{event.start_seconds.toFixed(2)}–{event.end_seconds.toFixed(2)} s</strong><span>{event.state==='incomplete_source_gap'?'Partial context':event.state}</span><small>{new Date(event.created_utc).toLocaleString()}</small>
      </button>)}
    <div className="controls"><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>Previous page</button><span>Page {page}</span><button disabled={events.length<20} onClick={()=>setPage(p=>p+1)}>Next page</button></div></aside><section className="panel detail-panel">
      {!detail?<p>Select an event to inspect its preserved source, trajectory, assessment and review history.</p>:<>
        <h3>Event detail <span className="badge">{detail.capture?.complete?'Complete context':'Incomplete context'}</span></h3>
        <p className="notice">{detail.capture?.limitations.join(' · ')||'Available context preserved.'} The short demo cannot supply 30 seconds before and 45 seconds after.</p>
        <label>Source segment <select value={segment} onChange={e=>{setSegment(e.target.value);setPlayTime(0);}}>{detail.segments.map(s=><option key={s.id} value={s.id}>{s.start_seconds.toFixed(3)}–{s.end_seconds.toFixed(3)} s</option>)}</select></label>
        {segment&&<><div className="media-wrap" style={{aspectRatio:`${width}/${height}`}}>
          <video key={segment} controls src={`/api/v1/events/${detail.event_id}/segments/${segment}/preview.mp4`} onTimeUpdate={e=>setPlayTime(e.currentTarget.currentTime)} onError={()=>setError('Playback unavailable. The source download is separate; inspect integrity before continuing.')} />
          {overlay&&<svg viewBox={`0 0 ${width} ${height}`} aria-label="Analysis trajectory overlay">{tracks.map((track,index)=>{
            const path=track.observations.filter(p=>p.time_seconds<=time);
            const point=path[path.length-1];
            return <g key={index} stroke={index%2?'#f2bd76':'#80e1d0'} fill="none"><polyline strokeWidth="1" points={path.map(p=>`${p.x_px},${p.y_px}`).join(' ')}/>{point&&Math.abs(time-point.time_seconds)<.2&&<circle cx={point.x_px} cy={point.y_px} r="4" strokeWidth="1"/>}</g>;
          })}</svg>}
        </div><label><input type="checkbox" checked={overlay} onChange={e=>setOverlay(e.target.checked)}/>Analysis overlay — raw observed centers</label>
        <p className="muted">MP4 playback is a derived preview. <a href={`/api/v1/events/${detail.event_id}/segments/${segment}/source`}>Download unmodified archived source segment</a></p></>}
        <details><summary>Evidence integrity and source references</summary><p className="hash">Capture SHA256: {detail.capture_manifest_sha256}</p>{detail.segments.map(s=><p className="hash" key={s.id}>{s.id} · {s.bytes} bytes<br/>{s.sha256}</p>)}<p>Exposure UTC unknown. Media time is based on source PTS. Calibration: {detail.calibration.reason}.</p></details>
        <h4>Assessment</h4>{detail.analyses.map(a=><p key={a.id}>{a.data.class}<br/><span className="muted">{a.data.reason_codes.join(' · ')}</span></p>)}
        <h4>Review</h4><label>Assessment <select value={assessment} onChange={e=>setAssessment(e.target.value)}><option value="unresolved_insufficient_data">Unresolved — missing data</option><option value="likely_conventional">Likely conventional</option><option value="likely_artifact">Likely image artifact</option><option value="unresolved_review_candidate">Review candidate</option><option value="reviewed_identified">Reviewed and identified</option></select></label>
        <label>Review note<textarea value={note} maxLength={4000} onChange={e=>setNote(e.target.value)} placeholder="Record what you observed and why"/></label>
        <button onClick={()=>void mutate(`events/${detail.event_id}/reviews`,'POST',{assessment,note}).then(()=>{setNote('');return open(detail.event_id);}).catch(e=>setError(String(e)))}>Save review</button>
        <ul>{detail.reviews.map(r=><li key={r.id}>{r.assessment}: {r.note}</li>)}</ul>
        <button onClick={()=>void mutate<{download_url:string;verification:{verified:boolean}}>(`events/${detail.event_id}/export`,'POST').then(r=>{if(!r.verification.verified)throw Error('Verification failed');setDownload(r.download_url);}).catch(e=>setError(String(e)))}>Export and verify evidence</button>
        {download&&<p role="status">Package verification passed. <a href={download}>Download evidence ZIP</a></p>}
      </>}
    </section></div>
    <details className="panel settings"><summary>Detector settings and exclusion mask</summary><p>Detection exclusions affect analysis. Original video retains those pixels. These settings apply to the next fixture analysis.</p>
      {settings&&<><label>Mode<select value={String(settings.detector.mode)} onChange={e=>setSettings({...settings,detector:{...settings.detector,mode:e.target.value}})}><option value="compact">Compact bright source</option><option value="difference">Temporal difference</option><option value="background">Background model</option></select></label>
      <label>Contrast threshold<input type="number" min="1" max="255" value={Number(settings.detector.contrast)} onChange={e=>setSettings({...settings,detector:{...settings.detector,contrast:Number(e.target.value)}})}/></label>
      <label>Exclusion rectangles (JSON: [[x,y,width,height]])<textarea aria-label="Exclusion rectangles" value={mask||JSON.stringify(settings.rectangles)} onChange={e=>setMask(e.target.value)}/></label>
      <button onClick={()=>{try{const rectangles=JSON.parse(mask||JSON.stringify(settings.rectangles));void mutate<Settings>('settings','PUT',{detector:settings.detector,rectangles}).then(s=>{setSettings(s);setMask('');}).catch(e=>setError(String(e)));}catch(e){setError(String(e));}}}>Save detector settings</button><p className="muted">Settings revision {settings.revision}. Mask coordinates use the native 320 × 180 fixture.</p></>}
    </details>
  </section>;
}
