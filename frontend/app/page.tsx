'use client';
import { useEffect, useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
type Scene = {id:number; narration:string; visual_prompt:string; duration:number; status:string; clip?:string};
type Project = {id:string; status:string; stage:string; error?:string; revision?:number; scenes:Scene[]; assets:Record<string,string>; request:{video_provider:string; voice_provider:string; planner_provider:string}};

export default function Home() {
  const [topic, setTopic] = useState('Black holes');
  const [duration, setDuration] = useState(30);
  const [language, setLanguage] = useState('English');
  const [style, setStyle] = useState('Cinematic documentary');
  const [instructions, setInstructions] = useState('');
  const [video, setVideo] = useState('preview');
  const [voice, setVoice] = useState('silent');
  const [planner, setPlanner] = useState('template');
  const [id, setId] = useState('');
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState<number | null>(null);
  const [edit, setEdit] = useState<Record<number, string>>({});
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetch(`${API}/projects/${id}`, {cache: 'no-store'});
        if (res.ok && !cancelled) setProject(await res.json());
      } catch { if (!cancelled) setError('Cannot reach the backend at ' + API); }
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [id]);
  async function start() {
    setBusy(true); setError(''); setProject(null);
    try {
      const res = await fetch(`${API}/projects`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({topic,duration,language,style,instructions,video_provider:video,voice_provider:voice,planner_provider:planner})});
      if (!res.ok) throw new Error(await res.text());
      const data:Project = await res.json(); setId(data.id); setProject(data);
      history.replaceState(null, '', `?project=${data.id}`);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function redo(scene:Scene) {
    if (!id) return;
    setError('');
    try {
      const res = await fetch(`${API}/projects/${id}/scenes/${scene.id}/regenerate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({visual_prompt:edit[scene.id] ?? scene.visual_prompt})});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }
  async function jobAction(action: 'cancel' | 'retry') {
    if (!id) return;
    setError('');
    try {
      const res = await fetch(`${API}/projects/${id}/${action}`, {method:'POST'});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }
  async function importClip(scene:Scene, file:File) {
    if (!id) return;
    setError(''); setUploading(scene.id);
    try {
      const res = await fetch(`${API}/projects/${id}/scenes/${scene.id}/clip`, {method:'POST', headers:{'Content-Type':file.type || 'application/octet-stream'}, body:file});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); } finally { setUploading(null); }
  }
  useEffect(() => { const query = new URLSearchParams(window.location.search).get('project'); if (query) setId(query); }, []);
  const asset = (path:string) => `${API}/projects/${id}/assets/${path}`;
  return <main>
    <header><span className="eyebrow">LOCAL VIDEO WORKSHOP / PHASE 1</span><h1>Content Factory</h1><p>Plan scenes, generate clips, add narration and captions, and keep every piece editable.</p></header>
    <section className="grid"><div className="panel"><h2>Create a project</h2>
      <label>Topic<input value={topic} onChange={e=>setTopic(e.target.value)} /></label>
      <div className="row"><label>Duration (seconds)<input type="number" min="10" max="120" value={duration} onChange={e=>setDuration(Number(e.target.value))} /></label><label>Language<input value={language} onChange={e=>setLanguage(e.target.value)} /></label></div>
      <label>Style<input value={style} onChange={e=>setStyle(e.target.value)} /></label>
      <label>Optional direction<textarea value={instructions} onChange={e=>setInstructions(e.target.value)} rows={3}/></label>
      <div className="row"><label>Scene planner<select value={planner} onChange={e=>setPlanner(e.target.value)}><option value="template">Template · pipeline test</option><option value="ollama">Ollama · local LLM</option></select></label><label>Video engine<select value={video} onChange={e=>setVideo(e.target.value)}><option value="preview">Preview · test cards</option><option value="ltx25">LTX 2.5 · GPU required</option></select></label></div>
      <label>Narration<select value={voice} onChange={e=>setVoice(e.target.value)}><option value="silent">Silent · pipeline test</option><option value="kokoro">Kokoro · local model</option></select></label>
      <p className="note">No GPU? Create a preview project, then replace its scene clips with your own footage. Silent preview audio and estimated captions remain until narration is configured.</p>
      <button disabled={busy || !topic.trim()} onClick={start}>{busy?'Starting…':'Generate project →'}</button>
    </div><div className="panel output"><h2>Output</h2>
      {!project && <div className="empty">Your render and scene files will appear here.</div>}
      {project && <><div className="status"><strong>{project.status.toUpperCase()}</strong><span>{project.stage}</span></div><div className="id">Project {project.id}</div>
        {(project.status==='queued' || project.status==='running') && <button className="secondary" onClick={()=>jobAction('cancel')}>Cancel job</button>}
        {(project.status==='failed' || project.status==='cancelled') && <button className="secondary" onClick={()=>jobAction('retry')}>Retry job</button>}
        {project.error && <p className="error">{project.error}</p>}
        {project.status==='complete' && <><video controls src={asset('final.mp4')+`?v=${project.revision??0}`} playsInline /><div className="links"><a href={asset('final.mp4')}>Final MP4</a><a href={asset('timeline.json')}>Timeline JSON</a><a href={asset('captions.srt')}>Captions SRT</a></div></>}
        <div className="scenes">{project.scenes.map(s=><article key={s.id}><div className="scene-heading"><strong>Scene {s.id}</strong><small>{s.duration.toFixed(1)}s · {s.status}</small></div><p>{s.narration}</p><textarea aria-label={`Scene ${s.id} visual prompt`} value={edit[s.id]??s.visual_prompt} onChange={e=>setEdit({...edit,[s.id]:e.target.value})} rows={3}/>{s.clip && <a href={asset(s.clip)}>View clip ↗</a>}{project.status==='complete' && <label>Replace scene footage (MP4 or MOV)<input type="file" accept="video/mp4,video/quicktime,.mov" disabled={uploading===s.id} onChange={e=>{const file=e.target.files?.[0];if(file) void importClip(s,file);e.target.value='';}} />{uploading===s.id && 'Uploading…'}</label>}{project.status==='complete' && <button className="secondary" onClick={()=>redo(s)}>Regenerate scene</button>}</article>)}</div>
      </>}
    </div></section>{error && <div role="alert" className="error">{error}</div>}
    <footer>All project media and prompts are stored locally. The preview is a pipeline test; it is not AI video.</footer>
  </main>;
}
