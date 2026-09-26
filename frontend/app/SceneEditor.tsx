'use client';
import { useState } from 'react';

export type AudioMode = 'narration' | 'native' | 'hybrid';
export type TransitionMode = 'cut' | 'fade' | 'fade_white';
export type Scene = {
  id:number; narration:string; visual_prompt:string; duration:number; status:string;
  beat?:string; continuity?:string; transition?:TransitionMode; transition_duration?:number;
  audio_mode?:AudioMode; generation_mode?:'fast'|'quality'; clip?:string; voice?:string;
  original_clip?:string; original_voice?:string; clip_origin?:'generated'|'uploaded';
  voice_origin?:'generated'|'uploaded'; source_ids?:number[];
};
type Source = {id:number; title:string; url:string};
type Props = {
  scene:Scene; index:number; count:number; ready:boolean; videoProvider:string;
  selected:boolean; onSelect:(sceneId:number,selected:boolean)=>void;
  sources?:Source[]; asset:(path:string)=>string;
  onJson:(sceneId:number,route:string,payload:Record<string,unknown>)=>Promise<void>;
  onUpload:(sceneId:number,kind:'clip'|'narration/upload',file:File,text?:string)=>Promise<void>;
  onRegenerate:(scene:Scene,prompt:string)=>Promise<void>;
  onTransition:(scene:Scene,transition:TransitionMode,duration:number)=>Promise<void>;
  onMove:(sceneId:number,direction:-1|1)=>Promise<void>;
};

export default function SceneEditor({scene:s,index,count,ready,videoProvider,selected,onSelect,sources,asset,onJson,onUpload,onRegenerate,onTransition,onMove}:Props) {
  const [narration,setNarration] = useState<string|null>(null);
  const [prompt,setPrompt] = useState<string|null>(null);
  const [mode,setMode] = useState<AudioMode|null>(null);
  const [transition,setTransition] = useState<TransitionMode|null>(null);
  const [transitionDuration,setTransitionDuration] = useState<number|null>(null);
  const [clipFile,setClipFile] = useState<File|null>(null);
  const [voiceFile,setVoiceFile] = useState<File|null>(null);
  const activeMode = mode ?? s.audio_mode ?? 'narration';
  const activeTransition = transition ?? s.transition ?? 'cut';
  return <article>
    <div className="scene-heading"><label><input type="checkbox" checked={selected} onChange={e=>onSelect(s.id,e.target.checked)} /> <strong>Scene {s.id}{s.beat?` · ${s.beat}`:''}</strong></label>
      <small>{s.duration.toFixed(1)}s · {s.status} · clip: {s.clip_origin??'generated'}</small></div>
    {!!s.source_ids?.length && <div className="links">Sources: {s.source_ids.map(id=>{
      const source=sources?.find(item=>item.id===id);
      return source && <a key={id} href={source.url} target="_blank" rel="noopener noreferrer">{source.title} ↗</a>;
    })}</div>}
    {s.continuity && <p className="note"><strong>Continuity:</strong> {s.continuity}</p>}
    <div className="story"><h3>Video</h3>
      {s.clip && <><video controls preload="metadata" src={asset(s.clip)} playsInline />
        <div className="links"><a href={asset(s.clip)}>Current clip ↗</a>
          {s.original_clip && s.original_clip!==s.clip && <a href={asset(s.original_clip)}>Original generated clip ↗</a>}</div></>}
      <label>Visual prompt<textarea value={prompt??s.visual_prompt} onChange={e=>setPrompt(e.target.value)} rows={3} /></label>
      <button className="secondary" disabled={!ready} onClick={()=>onRegenerate(s,prompt??s.visual_prompt)}>
        Regenerate video scene (AI video model)</button>
      <label>Replacement clip (MP4, MOV, WebM, MKV; 150 MB max)
        <input type="file" accept=".mp4,.mov,.webm,.mkv,video/*" onChange={e=>setClipFile(e.target.files?.[0]??null)} /></label>
      <button className="secondary" disabled={!ready||!clipFile} onClick={()=>clipFile&&onUpload(s.id,'clip',clipFile)}>
        Replace clip and rerender (no video model)</button>
    </div>
    <div className="story"><h3>Scene audio</h3>
      <label>Audio mode<select value={activeMode} onChange={e=>setMode(e.target.value as AudioMode)}>
        <option value="narration">Narration</option>
        <option value="hybrid" disabled={videoProvider!=='ltx25' && !s.clip_origin?.includes('uploaded')}>Hybrid · narration + clip ambience</option>
        <option value="native" disabled={videoProvider!=='ltx25' && s.clip_origin!=='uploaded'}>Native · clip audio</option>
      </select></label>
      <button className="secondary" disabled={!ready||mode===null||mode===s.audio_mode}
        onClick={()=>onJson(s.id,'audio-mode',{audio_mode:activeMode})}>Apply audio mode (rerender; TTS if missing)</button>
      {s.audio_mode==='native'
        ? <p className="note">Dialogue is embedded in this clip. To edit spoken words, switch to narration/hybrid or regenerate the video. Script captions may differ from replacement clip audio.</p>
        : <><label>Narration text<textarea value={narration??s.narration} onChange={e=>setNarration(e.target.value)} rows={3} /></label>
          <button className="secondary" disabled={!ready||!narration?.trim()||narration===s.narration}
            onClick={()=>onJson(s.id,'narration',{narration:narration?.trim()})}>Apply text (TTS this scene only)</button>
          <label>Replacement narration audio (30 MB max)
            <input type="file" accept=".wav,.mp3,.m4a,.flac,.ogg,.opus,audio/*" onChange={e=>setVoiceFile(e.target.files?.[0]??null)} /></label>
          <button className="secondary" disabled={!ready||!voiceFile}
            onClick={()=>voiceFile&&onUpload(s.id,'narration/upload',voiceFile,narration??undefined)}>
            Replace narration audio (render only)</button>
          <div className="links">{s.voice && <a href={asset(s.voice)}>Current narration ↗</a>}
            {s.original_voice && s.original_voice!==s.voice && <a href={asset(s.original_voice)}>Original narration ↗</a>}</div></>}
    </div>
    {index>0 && <div className="story"><h3>Transition into this scene</h3>
      <div className="row"><label>Transition<select value={activeTransition} onChange={e=>setTransition(e.target.value as TransitionMode)}>
        <option value="cut">Cut</option><option value="fade">Fade through black</option><option value="fade_white">Fade through white</option>
      </select></label><label>Duration (s)<input type="number" min="0.2" max="2" step="0.1"
        disabled={activeTransition==='cut'} value={(transitionDuration??s.transition_duration)||0.8}
        onChange={e=>setTransitionDuration(Number(e.target.value))} /></label></div>
      <button className="secondary" disabled={!ready}
        onClick={()=>onTransition(s,activeTransition,activeTransition==='cut'?0:(transitionDuration??s.transition_duration)||0.8)}>
        Apply transition (render only)</button>
    </div>}
    <div className="links"><button className="secondary" disabled={!ready||index===0} onClick={()=>onMove(s.id,-1)}>Move earlier</button>
      <button className="secondary" disabled={!ready||index===count-1} onClick={()=>onMove(s.id,1)}>Move later</button></div>
  </article>;
}
