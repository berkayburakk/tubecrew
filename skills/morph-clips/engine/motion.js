/* morph-clips engine — one shape that never cuts.
   Every style is a pure function of time: draw(t) paints the frame at time t.
   No CSS transitions, no timers, no state carried between frames, so any frame
   can be rendered on its own (which is what makes motion blur possible). */
(function(){
const M = window.M = {};
M.brand = window.BRAND || {};
const clamp = M.clamp = (x,a=0,b=1)=>Math.min(b,Math.max(a,x));
M.lerp = (a,b,u)=>a+(b-a)*u;
const easeOut = M.easeOut = x=>{x=clamp(x);return 1-Math.pow(1-x,3)};
const easeInOut = M.easeInOut = x=>{x=clamp(x);return x*x*x*(x*(6*x-15)+10)};

// Closed-form spring step response 0 → 1, zero initial velocity. w: stiffness, z: damping ratio.
const spring = M.spring = (tau,w,z)=>{
  if(tau<=0) return 0;
  if(!isFinite(w)) return 1;
  if(z<1){const wd=w*Math.sqrt(1-z*z);return 1-Math.exp(-z*w*tau)*(Math.cos(wd*tau)+z*w/wd*Math.sin(wd*tau));}
  return 1-Math.exp(-w*tau)*(1+w*tau);
};
// Spring presets [stiffness, damping]. FAST for leading edges, SLOW for trailing edges.
M.SPRING = {MORPH:[15,0.84], FAST:[27,0.86], SLOW:[12.5,0.9], SOFT:[10,0.95], CAMERA:[7.5,1], INSTANT:[Infinity,1]};

// A value that changes target many times = the sum of one spring per change.
// With period>0 and a closed loop, the previous cycle's residue is added so t=period equals t=0.
M.track = (v0,keys,preset=M.SPRING.MORPH,period=0)=>{
  let prev=v0; const ks=[];
  for(const k of keys){const d=k[1]-prev; prev=k[1]; const s=k[2]||preset; if(d!==0) ks.push([k[0],d,s[0],s[1]]);}
  const loop = period>0 && Math.abs(prev-v0)<1e-9;
  return t=>{let v=v0; for(const k of ks){v+=k[1]*spring(t-k[0],k[2],k[3]); if(loop) v+=k[1]*(spring(t+period-k[0],k[2],k[3])-1);} return v;};
};
const rgb = M.rgb = c=>{
  const h=String(c).trim();
  if(h[0]==='#'){const x=h.length<=5?h.slice(1,4).split('').map(d=>d+d).join(''):h.slice(1,7);return [0,2,4].map(i=>parseInt(x.slice(i,i+2),16));}
  const m=h.match(/\d+(\.\d+)?/g); return m?m.slice(0,3).map(Number):[0,0,0];
};
M.colorTrack = (c0,keys,preset=[20,1],period=0)=>{
  const a=rgb(c0); const ch=[0,1,2].map(i=>M.track(a[i],keys.map(k=>[k[0],rgb(k[1])[i],k[2]]),preset,period));
  return t=>`rgb(${ch.map(f=>Math.round(clamp(f(t),0,255))).join(',')})`;
};
// Step: the latest value whose time has passed.
M.step = (v0,keys)=>t=>{let v=v0; for(const k of keys) if(t>=k[0]) v=k[1]; return v;};

// Content swap: the exit blurs out fast; the entry waits `delay`, then sharpens over `enter`.
// Each side has its own timing, so old and new content never overlap.
M.show = (t,tin,tout,o={})=>{
  const delay=o.delay??0.07, enter=o.enter??0.26, exit=o.exit??0.12, bl=o.blur??12;
  const a = tin==null?1:easeOut((t-tin-delay)/enter);
  const b = tout==null?0:easeOut((t-tout)/exit);
  return {o:a*(1-b), blur:(1-a)*bl+b*bl*0.8, s:(0.94+0.06*a)*(1-0.03*b), a, b};
};
M.apply = (el,v,extra='')=>{
  if(v.o<0.002){el.style.display='none';return false;}
  el.style.display=''; el.style.opacity=v.o.toFixed(4);
  el.style.filter=v.blur>0.05?`blur(${v.blur.toFixed(2)}px)`:'none';
  el.style.transform=`scale(${v.s.toFixed(4)})${extra}`;
  return true;
};
M.text = (el,s)=>{ if(el._txt!==s){ el.textContent=s; el._txt=s; } };

// One icon set on a 24 grid; stroke width is normalised so every icon has the same weight.
// Paths adapted from Lucide (ISC). Add new ones on the same grid.
M.ICONS = {
 arrow:['M5 12h14','M13 5l7 7-7 7'], check:['M20 6 9 17l-5-5'], x:['M18 6 6 18','M6 6l12 12'], plus:['M5 12h14','M12 5v14'],
 folder:['M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z'],
 terminal:['m4 17 6-6-6-6','M12 19h8'], file:['M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z','M14 2v4a2 2 0 0 0 2 2h4','M8 13h8','M8 17h5'],
 pencil:['M21.17 6.81a1 1 0 0 0-3.99-3.99L3.84 16.17a2 2 0 0 0-.5.83l-1.32 4.35a.5.5 0 0 0 .62.62l4.35-1.32a2 2 0 0 0 .83-.5z','m15 5 4 4'],
 clock:['M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z','M12 6v6l4 2'],
 chip:['M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z','M9 9h6v6H9z','M9 1v3','M15 1v3','M9 20v3','M15 20v3','M20 9h3','M20 14h3','M1 9h3','M1 14h3'],
 sparkle:['M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z'],
 search:['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z','m21 21-4.3-4.3'],
 book:['M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20'],
 brain:['M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z','M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z','M12 5v13'],
 plug:['M12 22v-5','M9 8V2','M15 8V2','M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z'],
 layers:['m12 2 10 5-10 5L2 7Z','m2 17 10 5 10-5','m2 12 10 5 10-5'],
 lock:['M5 11h14v11H5z','M7 11V7a5 5 0 0 1 10 0v4'],
 alert:['m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3','M12 9v4','M12 17h.01'],
};
M.icon = (name,size,color,weight=2.2)=>`<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${color}" stroke-width="${(weight*24/size).toFixed(3)}" stroke-linecap="round" stroke-linejoin="round">${M.ICONS[name].map(d=>`<path d="${d}"/>`).join('')}</svg>`;

// Point path: world-space keys [t,x,y], eased between keys with a slight human arc.
M.path = (K)=>t=>{
  if(t<=K[0][0]) return {x:K[0][1],y:K[0][2]};
  const L=K[K.length-1]; if(t>=L[0]) return {x:L[1],y:L[2]};
  let i=0; while(t>=K[i+1][0]) i++;
  const [t0,x0,y0]=K[i],[t1,x1,y1]=K[i+1];
  const u=easeInOut((t-t0)/(t1-t0)), dx=x1-x0, dy=y1-y0, arc=Math.sin(Math.PI*u)*0.06;
  return {x:x0+dx*u-dy*arc, y:y0+dy*u+dx*arc};
};
M.presses = (clicks=[],drags=[])=>{
  const k=[];
  for(const c of clicks){k.push([c-0.07,1,[45,1]],[c+0.035,0,[22,0.72]]);}
  for(const [a,b] of drags){k.push([a-0.04,1,[45,1]],[b,0,[22,0.72]]);}
  k.sort((a,b)=>a[0]-b[0]); return M.track(0,k);
};
// When a rising value first crosses each threshold (precomputed in 1 ms steps).
M.crossings = (f,t0,t1,ths)=>ths.map(th=>{for(let t=t0;t<=t1;t+=0.001) if(f(t)>=th) return t; return Infinity;});

// Free area of the frame when some boxes will be covered later (e.g. a face cam restored in the composite):
// the largest strip beside the union of the boxes, kept `gap` px away from them. No boxes = the whole frame.
M.safeArea = (W,H,boxes=[],gap=48)=>{
  if(!boxes.length) return {x:0,y:0,w:W,h:H,cx:W/2,cy:H/2};
  const x0=Math.min(...boxes.map(b=>b[0])), y0=Math.min(...boxes.map(b=>b[1]));
  const x1=Math.max(...boxes.map(b=>b[0]+b[2])), y1=Math.max(...boxes.map(b=>b[1]+b[3]));
  const strips=[
    {x:0,y:0,w:Math.max(0,x0-gap),h:H}, {x:x1+gap,y:0,w:Math.max(0,W-x1-gap),h:H},
    {x:0,y:0,w:W,h:Math.max(0,y0-gap)}, {x:0,y:y1+gap,w:W,h:Math.max(0,H-y1-gap)},
  ];
  const b=strips.reduce((m,s)=>s.w*s.h>m.w*m.h?s:m);
  return {...b,cx:b.x+b.w/2,cy:b.y+b.h/2};
};

/* Scene runner
 c = { W,H,T, background:'#hex'|null (transparent)|undefined (brand canvas),
       center:[x,y] (default: centre of the free area, scaled to fit it), intro:t|null,
       shapes:{name:{w,h,r,fill,zoom}}, start:'name', flow:[[t,'name'],...],
       layers:[{el,in,out,anchor:'c'|'t'|'l',opts,update(t,g,v)}],
       cursor:{keys,clicks,drags,size}, press:[t], geom(t,g)->g, extra(t,g) }                */
M.scene = (c)=>{
  const $=id=>document.getElementById(id);
  const {W,H}=c;
  // Without an explicit center, the camera centres on the free area and scales the scene to fit it.
  const safe=M.safeArea(W,H,c.center?[]:(window.PROTECT||[]));
  // The fit uses the largest on-screen size of any state (w or h × zoom) plus a margin, not the frame size.
  const big=k=>Math.max(...Object.values(c.shapes).map(s=>s[k]*s.zoom));
  const [CX,CY]=c.center||[safe.cx,safe.cy], FIT=c.center||safe.w===W&&safe.h===H?1:Math.min(1,safe.w*0.92/big('w'),safe.h*0.92/big('h'));
  const stage=$('stage'), world=$('world'), shape=$('shape'), cur=$('cursor');
  stage.style.width=W+'px'; stage.style.height=H+'px'; $('wrap').style.width=W+'px'; $('wrap').style.height=H+'px';
  const bg = c.background===undefined ? (M.brand.canvas||'#101416') : c.background;
  stage.style.background=bg||'transparent';
  if(!bg) document.documentElement.classList.add('alpha');
  const S0=c.shapes[c.start], FL=c.flow||[];
  const sp=c.spring||M.SPRING.MORPH;
  const tr=k=>M.track(S0[k],FL.map(([t,n])=>[t,c.shapes[n][k]]),sp);
  const sW=tr('w'), sH=tr('h'), sR=tr('r');
  const sFill=M.colorTrack(S0.fill,FL.map(([t,n])=>[t,c.shapes[n].fill]),[20,1]);
  const zoom=M.track(S0.zoom,FL.map(([t,n])=>[t,c.shapes[n].zoom]),c.cameraSpring||M.SPRING.CAMERA);
  const press=M.track(0,(c.press||[]).flatMap(x=>[[x-0.07,1,[45,1]],[x+0.035,0,[22,.72]]]));
  const intro=c.intro===undefined?0.02:c.intro;
  const cp=c.cursor?M.path(c.cursor.keys):null, cpress=c.cursor?M.presses(c.cursor.clicks,c.cursor.drags):null;
  if(c.cursor) cur.style.width=(c.cursor.size||44)+'px'; else cur.style.display='none';
  const layers=(c.layers||[]).map(L=>({...L,node:$(L.el)}));
  const draw=(t)=>{
    let g={t, w:sW(t), h:sH(t), r:sR(t), fill:sFill(t), s:zoom(t)*FIT, cx:0, cy:0, fx:0, fy:0, sc:1-0.035*press(t), op:1};
    if(intro!=null){const a=spring(t-intro,13,0.78); g.sc*=0.55+0.45*a; g.op=clamp((t-intro)/0.12);}
    g.cursor=cp?cp(t):null;
    if(c.geom) g=c.geom(t,g)||g;
    g.r=Math.min(g.r,g.h/2,g.w/2);
    world.style.transform=`translate(${(CX-g.s*g.fx).toFixed(3)}px,${(CY-g.s*g.fy).toFixed(3)}px) scale(${g.s.toFixed(5)})`;
    const st=shape.style;
    st.left=(g.cx-g.w/2).toFixed(3)+'px'; st.top=(g.cy-g.h/2).toFixed(3)+'px';
    st.width=g.w.toFixed(3)+'px'; st.height=g.h.toFixed(3)+'px'; st.borderRadius=g.r.toFixed(3)+'px';
    st.background=g.fill; st.transform=`scale(${g.sc.toFixed(5)})`; st.opacity=g.op.toFixed(4);
    for(const L of layers){
      const v=M.show(t,L.in,L.out,L.opts||{});
      if(M.apply(L.node,v)){
        const an=L.anchor||'c';
        L.node.style.left=(an==='l'?0:g.w/2).toFixed(3)+'px';
        L.node.style.top=(an==='t'?0:g.h/2).toFixed(3)+'px';
        if(L.update) L.update(t,g,v);
      }
    }
    if(c.extra) c.extra(t,g);
    if(cp){
      const p=g.cursor, sx=CX+g.s*(p.x-g.fx), sy=CY+g.s*(p.y-g.fy);
      cur.style.transform=`translate(${(sx-3).toFixed(2)}px,${(sy-3).toFixed(2)}px) scale(${(1-0.13*cpress(t)).toFixed(4)})`;
    }
  };
  window.draw=draw; window.DURATION=c.T;
  // Preview: loops in the browser, scaled to fit. With ?render only draw(t) is called from outside.
  const RENDER=location.search.includes('render');
  document.body.classList.add(RENDER?'render':'preview');
  draw(0);
  if(!RENDER){
    const fit=()=>{const k=Math.min(innerWidth/W,(innerHeight-30)/H);$('wrap').style.transform=`translate(-50%,-50%) scale(${k})`;};
    fit(); addEventListener('resize',fit);
    const t0=performance.now();
    const loop=()=>{draw(((performance.now()-t0)/1000)%(c.T+0.8));requestAnimationFrame(loop);};
    requestAnimationFrame(loop);
  }
  return draw;
};
})();
