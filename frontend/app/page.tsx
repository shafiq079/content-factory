'use client';
import { useEffect, useState } from 'react';
import SceneEditor, {type Scene, type TransitionMode, type AudioMode} from './SceneEditor';
import TimelineWorkspace from './TimelineWorkspace';

const API = process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, '');
if (!API) {
  throw new Error('NEXT_PUBLIC_API_URL is not configured. Set it in frontend/.env.local.');
}
type CaptionStyle = 'classic' | 'bold' | 'minimal';
type GenerationMode = 'fast' | 'quality';
type MusicTrack = {provider:'uploaded'; asset:string; enabled:boolean; volume:number; loop:boolean; fade_in:number; fade_out:number};
type SFXTrack = {id:string; provider:'uploaded'; asset:string; enabled:boolean; start:number; duration?:number; volume:number; fade_in:number; fade_out:number};
type Source = {id:number; title:string; url:string; excerpt:string; evidence?:string; domain?:string; publisher?:string; published_at?:string|null; retrieved_at?:string|null; provider?:string};
type ResearchBrief = {mode:string; limitations:string[]; conflicts:{source_ids:number[];summary:string}[]};
type ClaimReviewItem = {scene_id:number; factual:boolean; verdict:'approved'|'revised'|'blocked'; source_ids:number[]; claims:string[]; reason:string; original_narration:string; reviewed_narration:string};
type ClaimReview = {status:'unreviewed'|'not_required'|'approved'|'approved_after_revision'|'blocked'; reviewer:string; reviewed_at?:string|null; fingerprint:string; attempts:number; items:ClaimReviewItem[]; limitations:string[]};
type Project = {id:string; status:string; stage:string; error?:string; revision?:number; caption_style?:CaptionStyle; pending_job?:{kind:string; execution?:string}; scenes:Scene[]; assets:Record<string,string>; idea?:string; hook?:string; script?:string; story_arc?:string; visual_bible?:string; research?:Source[]; research_brief?:ResearchBrief; claim_review?:ClaimReview; music?:MusicTrack|null; sfx?:SFXTrack[]; request:{video_provider:string; voice_provider:string; planner_provider:string; generation_mode?:GenerationMode; language?:string; voice_id?:string; voice_speed?:number}};

export default function Home() {
  const [topic, setTopic] = useState('Black holes');
  const [duration, setDuration] = useState(30);
  const [language, setLanguage] = useState('English');
  const [style, setStyle] = useState('Cinematic documentary');
  const [instructions, setInstructions] = useState('');
  const [video, setVideo] = useState('preview');
  const [generationMode, setGenerationMode] = useState<GenerationMode>('fast');
  const [voice, setVoice] = useState('silent');
  const [voiceId, setVoiceId] = useState('');
  const [voiceSpeed, setVoiceSpeed] = useState(1);
  const [planner, setPlanner] = useState('template');
  const [research, setResearch] = useState('auto');
  const [id, setId] = useState('');
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selectedScenes, setSelectedScenes] = useState<number[]>([]);
  const [activeSceneId, setActiveSceneId] = useState<number|null>(null);
  const [batchOperation, setBatchOperation] = useState<'set_transition'|'set_audio_mode'>('set_transition');
  const [batchTransition, setBatchTransition] = useState<TransitionMode>('cut');
  const [batchDuration, setBatchDuration] = useState(0.8);
  const [batchAudioMode, setBatchAudioMode] = useState<AudioMode>('narration');
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle | null>(null);
  const [projectVoiceId, setProjectVoiceId] = useState<string | null>(null);
  const [projectVoiceSpeed, setProjectVoiceSpeed] = useState<number | null>(null);
  const [musicFile, setMusicFile] = useState<File | null>(null);
  const [musicVolume, setMusicVolume] = useState<number | null>(null);
  const [musicLoop, setMusicLoop] = useState<boolean | null>(null);
  const [musicFadeIn, setMusicFadeIn] = useState<number | null>(null);
  const [musicFadeOut, setMusicFadeOut] = useState<number | null>(null);
  const [sfxFile, setSfxFile] = useState<File | null>(null);
  const [sfxStart, setSfxStart] = useState(0);
  const [sfxVolume, setSfxVolume] = useState(0.7);
  const [sfxFadeIn, setSfxFadeIn] = useState(0);
  const [sfxFadeOut, setSfxFadeOut] = useState(0.3);
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
    setBusy(true); setError(''); setProject(null); setSelectedScenes([]); setActiveSceneId(null);
    try {
      const res = await fetch(`${API}/projects`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({topic,duration,language,style,instructions,video_provider:video,generation_mode:video==='ltx25'?generationMode:'fast',voice_provider:voice,voice_id:voice==='kokoro'?voiceId:'',voice_speed:voice==='kokoro'?voiceSpeed:1,planner_provider:planner,research_provider:research})});
      if (!res.ok) throw new Error(await res.text());
      const data:Project = await res.json(); setId(data.id); setProject(data);
      history.replaceState(null, '', `?project=${data.id}`);
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function redo(scene:Scene, prompt:string) {
    if (!id) return;
    setError('');
    try {
      const res = await fetch(`${API}/projects/${id}/scenes/${scene.id}/regenerate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({visual_prompt:prompt})});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }
  async function sceneAction(sceneId:number, route:string, payload:Record<string,unknown>) {
    if (!id) return;
    setError('');
    try {
      const res = await fetch(`${API}/projects/${id}/scenes/${sceneId}/${route}`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }
  async function uploadSceneAsset(sceneId:number, kind:'clip'|'narration/upload', selected:File, text?:string) {
    if (!id) return;
    setError('');
    const form = new FormData();
    form.append('file', selected);
    if (kind==='narration/upload' && text !== undefined) form.append('narration', text);
    try {
      const res = await fetch(`${API}/projects/${id}/scenes/${sceneId}/${kind}`, {method:'POST', body:form});
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
  async function applyVoice() {
    if (!id || !project || project.request.voice_provider !== 'kokoro') return;
    setError('');
    try {
      const res = await fetch(`${API}/projects/${id}/voice`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        voice_id: projectVoiceId ?? project.request.voice_id ?? '',
        voice_speed: projectVoiceSpeed ?? project.request.voice_speed ?? 1
      })});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json()); setProjectVoiceId(null); setProjectVoiceSpeed(null);
    } catch(e) { setError(String(e)); }
  }
  async function uploadMusic() {
    if (!id || !musicFile) return;
    setError('');
    const form = new FormData();
    form.append('file', musicFile);
    form.append('volume', String(musicVolume ?? project?.music?.volume ?? 0.2));
    form.append('loop', String(musicLoop ?? project?.music?.loop ?? true));
    form.append('fade_in', String(musicFadeIn ?? project?.music?.fade_in ?? 0.5));
    form.append('fade_out', String(musicFadeOut ?? project?.music?.fade_out ?? 3));
    try {
      const res = await fetch(API+'/projects/'+id+'/music', {method:'POST', body:form});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json()); setMusicFile(null);
    } catch(e) { setError(String(e)); }
  }

  async function updateMusic() {
    if (!id || !project?.music) return;
    setError('');
    try {
      const res = await fetch(API+'/projects/'+id+'/music/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        enabled:true,
        volume:musicVolume ?? project.music.volume,
        loop:musicLoop ?? project.music.loop,
        fade_in:musicFadeIn ?? project.music.fade_in,
        fade_out:musicFadeOut ?? project.music.fade_out
      })});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }

  async function removeMusic() {
    if (!id || !project?.music) return;
    setError('');
    try {
      const res = await fetch(API+'/projects/'+id+'/music/remove', {method:'POST'});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json()); setMusicVolume(null); setMusicLoop(null); setMusicFadeIn(null); setMusicFadeOut(null);
    } catch(e) { setError(String(e)); }
  }

  async function uploadSfx() {
    if (!id || !sfxFile) return;
    setError('');
    const form = new FormData();
    form.append('file', sfxFile);
    form.append('start', String(sfxStart));
    form.append('volume', String(sfxVolume));
    form.append('fade_in', String(sfxFadeIn));
    form.append('fade_out', String(sfxFadeOut));
    try {
      const res = await fetch(API+'/projects/'+id+'/sfx', {method:'POST', body:form});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json()); setSfxFile(null);
    } catch(e) { setError(String(e)); }
  }

  async function removeSfx(effectId:string) {
    if (!id) return;
    setError('');
    try {
      const res = await fetch(API+'/projects/'+id+'/sfx/'+effectId+'/remove', {method:'POST'});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }
  async function updateTransition(scene:Scene, mode:TransitionMode, duration:number) {
    if (!id || !project) return;
    setError('');
    try {
      const res = await fetch(API+'/projects/'+id+'/timeline', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
        transitions:[{scene_id:scene.id, transition:mode, transition_duration:duration}]
      })});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }

  async function applyBatch() {
    if (!id || !project || !selectedScenes.length) return;
    setError('');
    const payload = batchOperation==='set_transition'
      ? {operation:batchOperation,scene_ids:selectedScenes,transition:batchTransition,transition_duration:batchTransition==='cut'?0:batchDuration}
      : {operation:batchOperation,scene_ids:selectedScenes,audio_mode:batchAudioMode};
    try {
      const res = await fetch(API+'/projects/'+id+'/scenes/batch', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json()); setSelectedScenes([]);
    } catch(e) { setError(String(e)); }
  }

  function setSceneSelected(sceneId:number, selected:boolean) {
    setSelectedScenes(current=>selected
      ? [...new Set([...current,sceneId])]
      : current.filter(id=>id!==sceneId));
  }

  async function reorderScenes(order:number[]) {
    if (!id || !project) return;
    setError('');
    try {
      const res = await fetch(API+'/projects/'+id+'/timeline', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({scene_order:order})});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json());
    } catch(e) { setError(String(e)); }
  }

  async function moveScene(sceneId:number, direction:-1|1) {
    if (!project) return;
    const order = project.scenes.map(scene=>scene.id);
    const index = order.indexOf(sceneId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= order.length) return;
    [order[index], order[nextIndex]] = [order[nextIndex], order[index]];
    await reorderScenes(order);
  }
  async function renderAgain() {
    if (!id || !project) return;
    setError('');
    try {
      const res = await fetch(`${API}/projects/${id}/render`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({caption_style:captionStyle ?? project.caption_style ?? 'classic'})});
      if (!res.ok) throw new Error(await res.text());
      setProject(await res.json()); setCaptionStyle(null);
    } catch(e) { setError(String(e)); }
  }
  useEffect(() => { const query = new URLSearchParams(window.location.search).get('project'); if (query) setId(query); }, []);
  useEffect(() => {
    if (!project?.scenes.length) {
      setActiveSceneId(null);
      return;
    }
    if (!project.scenes.some(scene=>scene.id===activeSceneId)) setActiveSceneId(project.scenes[0].id);
  }, [project?.scenes, activeSceneId]);

  const asset = (path:string) => `${API}/projects/${id}/assets/${path}`;
  const activeScene = project?.scenes.find(scene=>scene.id===activeSceneId) ?? project?.scenes[0] ?? null;
  const activeSceneIndex = activeScene && project ? project.scenes.findIndex(scene=>scene.id===activeScene.id) : -1;

  return <main>
    <header><span className="eyebrow">LOCAL VIDEO WORKSHOP / PHASE 1</span><h1>Content Factory</h1><p>Plan scenes, generate clips, add narration and captions, and keep every piece editable.</p></header>
    {project && project.scenes.length>0 && <TimelineWorkspace
      project={project}
      asset={asset}
      activeSceneId={activeScene?.id ?? null}
      onActiveScene={setActiveSceneId}
      selectedScenes={selectedScenes}
      onSelectScene={setSceneSelected}
      onReorder={reorderScenes}
      inspector={activeScene && activeSceneIndex>=0 ? <SceneEditor
        key={`${activeScene.id}-${project.revision??0}`}
        scene={activeScene}
        index={activeSceneIndex}
        count={project.scenes.length}
        ready={project.status==='complete'}
        selected={selectedScenes.includes(activeScene.id)}
        onSelect={setSceneSelected}
        videoProvider={project.request.video_provider}
        sources={project.research}
        asset={asset}
        onJson={sceneAction}
        onUpload={uploadSceneAsset}
        onRegenerate={redo}
        onTransition={updateTransition}
        onMove={moveScene}
      /> : null}
    />}
    <section className="grid"><div className="panel"><h2>Create a project</h2>
      <label>Topic<input value={topic} onChange={e=>setTopic(e.target.value)} /></label>
      <div className="row"><label>Duration (seconds)<input type="number" min="10" max="120" value={duration} onChange={e=>setDuration(Number(e.target.value))} /></label><label>Language<input value={language} onChange={e=>setLanguage(e.target.value)} /></label></div>
      <label>Style<input value={style} onChange={e=>setStyle(e.target.value)} /></label>
      <label>Optional direction<textarea value={instructions} onChange={e=>setInstructions(e.target.value)} rows={3}/></label>
      <div className="row"><label>Scene planner<select value={planner} onChange={e=>setPlanner(e.target.value)}><option value="template">Template · pipeline test</option><option value="ollama">Ollama · local LLM</option></select></label><label>Video engine<select value={video} onChange={e=>{setVideo(e.target.value);if(e.target.value!=='ltx25')setGenerationMode('fast');}}><option value="preview">Preview · test cards</option><option value="ltx25">LTX 2.5 · GPU required</option><option value="wan22">Wan 2.2 TI2V-5B · GPU required</option></select></label></div>
      <label>LTX generation quality<select value={generationMode} disabled={video!=='ltx25'} onChange={e=>setGenerationMode(e.target.value as GenerationMode)}><option value="fast">Fast · Distilled</option><option value="quality">Quality · DFR production path</option></select></label>
      <label>Topic research<select value={research} onChange={e=>setResearch(e.target.value)}><option value="auto">Automatic · broader for Ollama, none for preview</option><option value="broader">Broader · local SearXNG + Wikipedia fallback</option><option value="wikipedia">Wikipedia references only</option><option value="none">No external research · creative/fiction</option></select></label>
      <label>Narration<select value={voice} onChange={e=>setVoice(e.target.value)}><option value="silent">Silent · pipeline test</option><option value="kokoro">Kokoro · local model</option></select></label>
      {voice==='kokoro' && <><div className="row"><label>Kokoro voice ID<input value={voiceId} onChange={e=>setVoiceId(e.target.value)} placeholder="Blank = language default, e.g. af_heart" /></label><label>Voice speed<input type="number" min="0.5" max="2" step="0.05" value={voiceSpeed} onChange={e=>setVoiceSpeed(Number(e.target.value))} /></label></div><p className="note">Voice ID may also be a comma-separated Kokoro voice blend. It must match the selected language. Speed 1.0 is normal.</p></>}
      <p className="note">For real content use Ollama and Kokoro with LTX 2.5 or Wan 2.2. Wan makes video without native sound; scenes use narration. LTX Fast uses Distilled; Quality uses DFR. Preview mode makes colored test clips with silent audio.</p>
      <button disabled={busy || !topic.trim()} onClick={start}>{busy?'Starting…':'Generate project →'}</button>
    </div><div className="panel output"><h2>Output</h2>
      {!project && <div className="empty">Your render and scene files will appear here.</div>}
      {project && <><div className="status"><strong>{project.status.toUpperCase()}</strong><span>{project.stage}{project.pending_job?.kind==='batch' && project.pending_job.execution?` · ${project.pending_job.execution}`:''}</span></div><div className="id">Project {project.id}</div>
        {(project.status==='queued' || project.status==='running') && <button className="secondary" onClick={()=>jobAction('cancel')}>Cancel job</button>}
        {(project.status==='failed' || project.status==='cancelled') && <button className="secondary" onClick={()=>jobAction('retry')}>Retry job</button>}
        {project.error && <p className="error">{project.error}</p>}
        {project.status==='complete' && <><p className="note">Use the Program monitor and visual timeline above for playback, seeking and scene selection.</p><div className="links"><a href={asset('final.mp4')}>Final MP4</a><a href={asset('timeline.json')}>Timeline JSON</a><a href={asset('captions.srt')}>Captions SRT</a></div>{project.request.voice_provider==='kokoro' && <div className="story"><h3>Narration voice</h3><div className="row"><label>Kokoro voice ID<input value={projectVoiceId ?? project.request.voice_id ?? ''} onChange={e=>setProjectVoiceId(e.target.value)} /></label><label>Voice speed<input type="number" min="0.5" max="2" step="0.05" value={projectVoiceSpeed ?? project.request.voice_speed ?? 1} onChange={e=>setProjectVoiceSpeed(Number(e.target.value))} /></label></div><p className="note">Applying new voice settings regenerates narration and captions, then rerenders using the existing scene clips. It does not call the video model.</p><button className="secondary" onClick={applyVoice}>Apply voice settings</button></div>}<div className="story"><h3>Background music</h3><label>Music file<input type="file" accept=".mp3,.m4a,.aac,.wav,.flac,.ogg,.opus,audio/*" onChange={e=>setMusicFile(e.target.files?.[0]??null)} /></label><div className="row"><label>Volume<input type="number" min="0" max="1" step="0.05" value={musicVolume ?? project.music?.volume ?? 0.2} onChange={e=>setMusicVolume(Number(e.target.value))} /></label><label>Loop<select value={String(musicLoop ?? project.music?.loop ?? true)} onChange={e=>setMusicLoop(e.target.value==='true')}><option value="true">Loop to video length</option><option value="false">Play once</option></select></label></div><div className="row"><label>Fade in (s)<input type="number" min="0" max="30" step="0.1" value={musicFadeIn ?? project.music?.fade_in ?? 0.5} onChange={e=>setMusicFadeIn(Number(e.target.value))} /></label><label>Fade out (s)<input type="number" min="0" max="30" step="0.1" value={musicFadeOut ?? project.music?.fade_out ?? 3} onChange={e=>setMusicFadeOut(Number(e.target.value))} /></label></div>{project.music && <div className="links"><a href={asset(project.music.asset)} target="_blank" rel="noopener noreferrer">Current music ↗</a></div>}<div className="links"><button className="secondary" disabled={!musicFile} onClick={uploadMusic}>{project.music?'Replace music':'Add music'}</button>{project.music && <><button className="secondary" onClick={updateMusic}>Apply music settings</button><button className="secondary" onClick={removeMusic}>Remove music</button></>}</div><p className="note">Music is mixed after scene narration/native audio. Short tracks can loop; fades and volume are stored in the editable timeline.</p></div><div className="story"><h3>Sound effects</h3><label>SFX file<input type="file" accept=".mp3,.m4a,.aac,.wav,.flac,.ogg,.opus,audio/*" onChange={e=>setSfxFile(e.target.files?.[0]??null)} /></label><div className="row"><label>Start (s)<input type="number" min="0" step="0.1" value={sfxStart} onChange={e=>setSfxStart(Number(e.target.value))} /></label><label>Volume<input type="number" min="0" max="1" step="0.05" value={sfxVolume} onChange={e=>setSfxVolume(Number(e.target.value))} /></label></div><div className="row"><label>Fade in (s)<input type="number" min="0" max="30" step="0.1" value={sfxFadeIn} onChange={e=>setSfxFadeIn(Number(e.target.value))} /></label><label>Fade out (s)<input type="number" min="0" max="30" step="0.1" value={sfxFadeOut} onChange={e=>setSfxFadeOut(Number(e.target.value))} /></label></div><button className="secondary" disabled={!sfxFile} onClick={uploadSfx}>Add sound effect</button>{!!project.sfx?.length && <div className="scenes">{project.sfx.map(effect=><article key={effect.id}><div className="scene-heading"><strong>SFX</strong><small>{effect.start.toFixed(1)}s · volume {effect.volume.toFixed(2)}</small></div><div className="links"><a href={asset(effect.asset)} target="_blank" rel="noopener noreferrer">Audio asset ↗</a><button className="secondary" onClick={()=>removeSfx(effect.id)}>Remove</button></div></article>)}</div>}<p className="note">SFX are timeline overlays. Their start time and mix settings are saved separately from the scene clips.</p></div><label>Caption style<select value={captionStyle ?? project.caption_style ?? 'classic'} onChange={e=>setCaptionStyle(e.target.value as CaptionStyle)}><option value="classic">Classic</option><option value="bold">Bold</option><option value="minimal">Minimal</option></select></label><button className="secondary" onClick={renderAgain}>Render again with saved scenes</button></>}
        {project.script && <div className="story"><h3>Idea and script</h3><p><strong>{project.idea}</strong></p>{project.story_arc && <p><strong>Story arc:</strong> {project.story_arc}</p>}{project.visual_bible && <p><strong>Visual bible:</strong> {project.visual_bible}</p>}<p>{project.script}</p></div>}
        {!!project.research?.length && <div className="story"><h3>Research evidence</h3>
          <p className="note">Source passages are context for the script. The automated evidence review below checks whether narration appears supported by the saved evidence, but it does not prove the sources are true or complete.</p>
          {project.research_brief?.limitations.map((note,index)=><p className="note" key={index}>{note}</p>)}
          {project.research_brief?.conflicts.map((item,index)=><p className="error" key={index}>Possible source disagreement: {item.summary}</p>)}
          {project.claim_review && <div className="story"><strong>Automated claim review: {project.claim_review.status.replaceAll('_',' ')}</strong>
            <p className="note">{project.claim_review.reviewer!=='none'?`Reviewer: ${project.claim_review.reviewer} · attempts ${project.claim_review.attempts}`:'No factual review required for this project.'}</p>
            {project.claim_review.limitations.map((note,index)=><p className="note" key={index}>{note}</p>)}
            {project.claim_review.items.map(item=><div className="story" key={item.scene_id}><strong>Scene {item.scene_id} · {item.verdict}</strong>
              {item.factual && <p className="note">Evidence sources: {item.source_ids.join(', ')||'none'}</p>}
              {!!item.claims.length && <p>{item.claims.join(' · ')}</p>}
              {item.reason && <p className="note">{item.reason}</p>}
              {item.original_narration!==item.reviewed_narration && <p className="note">Auto-revised: “{item.original_narration}” → “{item.reviewed_narration}”</p>}
            </div>)}
          </div>}
          {project.research.map(source=><div key={source.id} className="story"><strong>Source {source.id} · </strong><a href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a>
            <p className="note">{source.publisher||source.domain||'Source'} · {source.provider||'reference'}{source.published_at?` · published ${source.published_at}`:''}
              {source.retrieved_at?` · retrieved ${source.retrieved_at.slice(0,10)}`:''}</p>
            <p>{source.evidence||source.excerpt}</p>
            <p className="note">Cited by scenes: {project.scenes.filter(scene=>scene.source_ids?.includes(source.id)).map(scene=>scene.id).join(', ')||'none'}</p>
            {source.domain==='en.wikipedia.org' && <p className="note">Wikipedia text: CC BY-SA 4.0.</p>}
          </div>)}</div>}
        {!!project.scenes.length && <div className="story"><h3>Batch scene edits</h3>
          <div className="links"><button className="secondary" onClick={()=>setSelectedScenes(project.scenes.map(scene=>scene.id))}>Select all</button>
            <button className="secondary" onClick={()=>setSelectedScenes([])}>Clear selection</button>
            <span>{selectedScenes.filter(sceneId=>project.scenes.some(scene=>scene.id===sceneId)).length} selected</span></div>
          <div className="row"><label>Action<select value={batchOperation} onChange={e=>setBatchOperation(e.target.value as typeof batchOperation)}>
            <option value="set_transition">Set transition</option><option value="set_audio_mode">Set audio mode</option>
          </select></label>
          {batchOperation==='set_transition' ? <><label>Transition<select value={batchTransition} onChange={e=>setBatchTransition(e.target.value as TransitionMode)}>
            <option value="cut">Cut</option><option value="fade">Fade through black</option><option value="fade_white">Fade through white</option>
          </select></label><label>Duration (s)<input type="number" min="0.2" max="2" step="0.1" disabled={batchTransition==='cut'} value={batchDuration} onChange={e=>setBatchDuration(Number(e.target.value))} /></label></>
            : <label>Audio mode<select value={batchAudioMode} onChange={e=>setBatchAudioMode(e.target.value as AudioMode)}>
              <option value="narration">Narration</option><option value="native">Native</option><option value="hybrid">Hybrid</option>
            </select></label>}</div>
          <p className="note">{batchOperation==='set_audio_mode' && batchAudioMode!=='native' && project.scenes.some(scene=>selectedScenes.includes(scene.id) && !scene.voice)
            ? 'TTS + render: selected scenes without narration need voice generation.'
            : 'Render only: use saved clips and audio.'} One job and one final render; no video generation. Fades cannot include the first scene. Native requires audio in every selected clip. The server validates all scenes before queueing.</p>
          <button className="secondary" disabled={project.status!=='complete'||!selectedScenes.length} onClick={applyBatch}>Apply to {selectedScenes.length} scenes</button>
        </div>}
      </>}
    </div></section>{error && <div role="alert" className="error">{error}</div>}
    <footer>All project media and prompts are stored locally. The preview is a pipeline test; it is not AI video.</footer>
  </main>;
}
