'use client';

import {useMemo, useRef, useState, type DragEvent, type MouseEvent, type ReactNode} from 'react';
import type {Scene} from './SceneEditor';

type MusicTrack = {
  provider:'uploaded';
  asset:string;
  enabled:boolean;
  volume:number;
  loop:boolean;
  fade_in:number;
  fade_out:number;
};

type SFXTrack = {
  id:string;
  provider:'uploaded';
  asset:string;
  enabled:boolean;
  start:number;
  duration?:number;
  volume:number;
  fade_in:number;
  fade_out:number;
};

type TimelineProject = {
  status:string;
  stage:string;
  revision?:number;
  scenes:Scene[];
  music?:MusicTrack|null;
  sfx?:SFXTrack[];
};

type Props = {
  project:TimelineProject;
  asset:(path:string)=>string;
  activeSceneId:number|null;
  onActiveScene:(sceneId:number)=>void;
  selectedScenes:number[];
  onSelectScene:(sceneId:number,selected:boolean)=>void;
  onReorder:(sceneOrder:number[])=>Promise<void>;
  inspector:ReactNode;
};

type ScenePosition = {
  scene:Scene;
  index:number;
  start:number;
  end:number;
  left:number;
  width:number;
};

function formatTime(value:number) {
  const safe = Number.isFinite(value) ? Math.max(0, value) : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = safe - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, '0')}`;
}

function chooseTickStep(total:number) {
  if (total <= 20) return 2;
  if (total <= 45) return 5;
  if (total <= 90) return 10;
  return 15;
}

export default function TimelineWorkspace({
  project,
  asset,
  activeSceneId,
  onActiveScene,
  selectedScenes,
  onSelectScene,
  onReorder,
  inspector,
}:Props) {
  const videoRef = useRef<HTMLVideoElement|null>(null);
  const [currentTime,setCurrentTime] = useState(0);
  const [playing,setPlaying] = useState(false);
  const [zoom,setZoom] = useState(1.25);
  const [draggedScene,setDraggedScene] = useState<number|null>(null);

  const totalDuration = useMemo(
    () => Math.max(0.1, project.scenes.reduce((sum,scene)=>sum + Math.max(0, scene.duration || 0), 0)),
    [project.scenes],
  );

  const positions = useMemo<ScenePosition[]>(() => {
    let cursor = 0;
    return project.scenes.map((scene,index) => {
      const start = cursor;
      const duration = Math.max(0, scene.duration || 0);
      cursor += duration;
      return {
        scene,
        index,
        start,
        end:cursor,
        left:(start / totalDuration) * 100,
        width:(duration / totalDuration) * 100,
      };
    });
  }, [project.scenes,totalDuration]);

  const tickStep = chooseTickStep(totalDuration);
  const ticks = useMemo(() => {
    const values:number[] = [];
    for (let value=0; value<=totalDuration + 0.001; value+=tickStep) values.push(value);
    if (values[values.length-1] < totalDuration) values.push(totalDuration);
    return values;
  }, [tickStep,totalDuration]);

  const activePosition = positions.find(item=>item.scene.id===activeSceneId) ?? positions[0];
  const timelineWidth = `${Math.max(100, zoom * 100)}%`;

  function seekTo(value:number) {
    const next = Math.max(0, Math.min(totalDuration, value));
    setCurrentTime(next);
    if (videoRef.current) videoRef.current.currentTime = next;
  }

  function seekFromPointer(event:MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
    seekTo(totalDuration * ratio);
  }

  async function togglePlay() {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) await video.play();
    else video.pause();
  }

  function focusScene(position:ScenePosition) {
    onActiveScene(position.scene.id);
    seekTo(position.start);
  }

  async function dropScene(targetSceneId:number) {
    if (draggedScene===null || draggedScene===targetSceneId) {
      setDraggedScene(null);
      return;
    }
    const order = project.scenes.map(scene=>scene.id);
    const fromIndex = order.indexOf(draggedScene);
    const targetIndex = order.indexOf(targetSceneId);
    if (fromIndex < 0 || targetIndex < 0) return;
    const [moved] = order.splice(fromIndex,1);
    order.splice(targetIndex,0,moved);
    setDraggedScene(null);
    await onReorder(order);
  }

  function dragOver(event:DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }

  const playhead = Math.max(0, Math.min(100, (currentTime / totalDuration) * 100));

  return <section className="editor-workspace" aria-label="Visual timeline editor">
    <div className="editor-topbar">
      <div>
        <span className="editor-kicker">VISUAL TIMELINE EDITOR</span>
        <strong>{project.status.toUpperCase()} · {project.stage}</strong>
      </div>
      <div className="editor-topbar-actions">
        <span>{selectedScenes.length} selected</span>
        <button className="editor-chip" onClick={()=>project.scenes.forEach(scene=>onSelectScene(scene.id,true))}>Select all</button>
        <button className="editor-chip" onClick={()=>project.scenes.forEach(scene=>onSelectScene(scene.id,false))}>Clear</button>
      </div>
    </div>

    <div className="editor-main">
      <aside className="editor-media-bin">
        <div className="editor-pane-title">
          <strong>Project media</strong>
          <small>{project.scenes.length} scenes</small>
        </div>
        <div className="editor-bin-list">
          {positions.map(position=><button
            key={position.scene.id}
            className={`editor-bin-item ${activePosition?.scene.id===position.scene.id?'active':''}`}
            onClick={()=>focusScene(position)}
          >
            <span className="editor-bin-index">{String(position.index+1).padStart(2,'0')}</span>
            <span>
              <strong>{position.scene.beat || `Scene ${position.scene.id}`}</strong>
              <small>{formatTime(position.scene.duration)} · {position.scene.audio_mode || 'narration'}</small>
            </span>
          </button>)}
        </div>
        <div className="editor-bin-links">
          {project.status==='complete' && <a href={asset('final.mp4')} target="_blank" rel="noopener noreferrer">Final MP4 ↗</a>}
          <a href={asset('timeline.json')} target="_blank" rel="noopener noreferrer">Timeline JSON ↗</a>
          <a href={asset('captions.srt')} target="_blank" rel="noopener noreferrer">Captions ↗</a>
        </div>
      </aside>

      <div className="editor-preview-pane">
        <div className="editor-pane-title">
          <strong>Program monitor</strong>
          <small>{formatTime(currentTime)} / {formatTime(totalDuration)}</small>
        </div>
        <div className="editor-monitor">
          {project.status==='complete'
            ? <video
                ref={videoRef}
                src={asset('final.mp4')+`?v=${project.revision??0}`}
                playsInline
                preload="metadata"
                onTimeUpdate={event=>setCurrentTime(event.currentTarget.currentTime)}
                onPlay={()=>setPlaying(true)}
                onPause={()=>setPlaying(false)}
                onEnded={()=>setPlaying(false)}
              />
            : <div className="editor-monitor-placeholder">
                <strong>{project.status==='failed'?'Render unavailable':'Preparing preview…'}</strong>
                <span>{project.stage}</span>
              </div>}
        </div>
        <div className="editor-transport">
          <button onClick={()=>seekTo(0)} disabled={project.status!=='complete'} title="Go to start">|◀</button>
          <button onClick={()=>seekTo(currentTime-5)} disabled={project.status!=='complete'} title="Back 5 seconds">−5s</button>
          <button className="editor-play" onClick={togglePlay} disabled={project.status!=='complete'}>{playing?'Pause':'Play'}</button>
          <button onClick={()=>seekTo(currentTime+5)} disabled={project.status!=='complete'} title="Forward 5 seconds">+5s</button>
          <button onClick={()=>seekTo(totalDuration)} disabled={project.status!=='complete'} title="Go to end">▶|</button>
        </div>
        <input
          className="editor-scrubber"
          aria-label="Playback position"
          type="range"
          min="0"
          max={totalDuration}
          step="0.05"
          value={Math.min(currentTime,totalDuration)}
          onChange={event=>seekTo(Number(event.target.value))}
          disabled={project.status!=='complete'}
        />
        <div className="editor-active-scene">
          <span>Active scene</span>
          <strong>{activePosition ? `Scene ${activePosition.scene.id} · ${activePosition.scene.beat || 'scene'}` : 'None'}</strong>
        </div>
      </div>

      <aside className="editor-inspector">
        <div className="editor-pane-title">
          <strong>Inspector</strong>
          <small>Scene controls</small>
        </div>
        <div className="editor-inspector-scroll">{inspector}</div>
      </aside>
    </div>

    <div className="timeline-panel">
      <div className="timeline-toolbar">
        <div>
          <strong>Timeline</strong>
          <span>Drag video blocks to reorder · click anywhere to seek</span>
        </div>
        <label className="timeline-zoom">Zoom
          <input type="range" min="1" max="4" step="0.25" value={zoom} onChange={event=>setZoom(Number(event.target.value))} />
        </label>
      </div>

      <div className="timeline-scroll">
        <div className="timeline-content" style={{width:timelineWidth}}>
          <div className="timeline-row timeline-ruler-row">
            <div className="timeline-track-label">TIME</div>
            <div className="timeline-track-canvas timeline-ruler" onClick={seekFromPointer}>
              {ticks.map(tick=><span key={tick} className="timeline-tick" style={{left:`${(tick/totalDuration)*100}%`}}>
                <i />
                <b>{formatTime(tick)}</b>
              </span>)}
              <span className="timeline-playhead" style={{left:`${playhead}%`}} />
            </div>
          </div>

          <div className="timeline-row">
            <div className="timeline-track-label"><strong>V1</strong><span>Video</span></div>
            <div className="timeline-track-canvas" onClick={seekFromPointer}>
              {positions.map(position=><button
                key={position.scene.id}
                draggable={project.status==='complete'}
                onDragStart={event=>{setDraggedScene(position.scene.id);event.dataTransfer.effectAllowed='move';}}
                onDragOver={dragOver}
                onDrop={()=>dropScene(position.scene.id)}
                onClick={event=>{event.stopPropagation();focusScene(position);}}
                className={[
                  'timeline-clip',
                  'timeline-video-clip',
                  activePosition?.scene.id===position.scene.id?'active':'',
                  selectedScenes.includes(position.scene.id)?'selected':'',
                  draggedScene===position.scene.id?'dragging':'',
                ].filter(Boolean).join(' ')}
                style={{left:`${position.left}%`,width:`${Math.max(position.width,0.6)}%`}}
                title={`Scene ${position.scene.id}: ${position.scene.visual_prompt}`}
              >
                <span className="timeline-clip-head">
                  <input
                    type="checkbox"
                    checked={selectedScenes.includes(position.scene.id)}
                    onClick={event=>event.stopPropagation()}
                    onChange={event=>onSelectScene(position.scene.id,event.target.checked)}
                    aria-label={`Select scene ${position.scene.id}`}
                  />
                  <b>S{position.scene.id}</b>
                </span>
                <span>{position.scene.beat || 'scene'}</span>
                <small>{position.scene.duration.toFixed(1)}s</small>
              </button>)}
              {positions.slice(1).map(position=>{
                const transition=position.scene.transition || 'cut';
                if (transition==='cut') return null;
                return <span
                  key={`transition-${position.scene.id}`}
                  className="timeline-transition"
                  style={{left:`${position.left}%`}}
                  title={`${transition} · ${position.scene.transition_duration ?? 0.8}s`}
                >◇</span>;
              })}
              <span className="timeline-playhead" style={{left:`${playhead}%`}} />
            </div>
          </div>

          <div className="timeline-row">
            <div className="timeline-track-label"><strong>A1</strong><span>Voice</span></div>
            <div className="timeline-track-canvas" onClick={seekFromPointer}>
              {positions.map(position=><button
                key={position.scene.id}
                className={`timeline-clip timeline-audio-clip ${activePosition?.scene.id===position.scene.id?'active':''}`}
                style={{left:`${position.left}%`,width:`${Math.max(position.width,0.6)}%`}}
                onClick={event=>{event.stopPropagation();focusScene(position);}}
                title={position.scene.narration}
              >
                <b>{position.scene.audio_mode==='native'?'Native audio':'Narration'}</b>
                <span>{position.scene.narration || 'No narration'}</span>
              </button>)}
              <span className="timeline-playhead" style={{left:`${playhead}%`}} />
            </div>
          </div>

          <div className="timeline-row">
            <div className="timeline-track-label"><strong>A2</strong><span>Music</span></div>
            <div className="timeline-track-canvas" onClick={seekFromPointer}>
              {project.music?.enabled
                ? <span className="timeline-clip timeline-music-clip" style={{left:'0%',width:'100%'}}>
                    <b>Background music</b>
                    <span>{project.music.loop?'Looped':'Play once'} · volume {project.music.volume.toFixed(2)}</span>
                  </span>
                : <span className="timeline-empty-track">No background music</span>}
              <span className="timeline-playhead" style={{left:`${playhead}%`}} />
            </div>
          </div>

          <div className="timeline-row">
            <div className="timeline-track-label"><strong>A3</strong><span>SFX</span></div>
            <div className="timeline-track-canvas" onClick={seekFromPointer}>
              {!!project.sfx?.length
                ? project.sfx.filter(effect=>effect.enabled).map(effect=>{
                    const left=(Math.max(0,effect.start)/totalDuration)*100;
                    const width=Math.max(1.2,((effect.duration ?? Math.min(1,totalDuration))/totalDuration)*100);
                    return <span key={effect.id} className="timeline-clip timeline-sfx-clip" style={{left:`${left}%`,width:`${width}%`}}>
                      <b>SFX</b><span>{effect.start.toFixed(1)}s</span>
                    </span>;
                  })
                : <span className="timeline-empty-track">No sound effects</span>}
              <span className="timeline-playhead" style={{left:`${playhead}%`}} />
            </div>
          </div>

          <div className="timeline-row">
            <div className="timeline-track-label"><strong>CC</strong><span>Captions</span></div>
            <div className="timeline-track-canvas" onClick={seekFromPointer}>
              {positions.map(position=><button
                key={position.scene.id}
                className="timeline-clip timeline-caption-clip"
                style={{left:`${position.left}%`,width:`${Math.max(position.width,0.6)}%`}}
                onClick={event=>{event.stopPropagation();focusScene(position);}}
                title={position.scene.narration}
              >
                <span>{position.scene.narration || '—'}</span>
              </button>)}
              <span className="timeline-playhead" style={{left:`${playhead}%`}} />
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>;
}
