import React, {useEffect, useRef, useState} from "react";
import {createRoot} from "react-dom/client";
import {
 Activity, RefreshCw, FileText, Settings, Shield, ChevronRight, X,
 Search, AlertTriangle, Server, Layers, CheckCircle2, CircleAlert,
 Play, Square, Stethoscope, Clock3
} from "lucide-react";
import "./style.css";

function Metric({label,value,detail,kind=""}){
 return <div className={`metric ${kind}`}>
  <small>{label}</small><strong>{value ?? "—"}</strong>
  {detail&&<span>{detail}</span>}
 </div>;
}

function App(){
 const [namespaces,setNamespaces]=useState([]),[ns,setNs]=useState(""),[pods,setPods]=useState([]),
 [overview,setOverview]=useState(null),[selected,setSelected]=useState(null),
 [action,setAction]=useState("Overview"),[payload,setPayload]=useState(null),
 [search,setSearch]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState(""),
 [logsText,setLogsText]=useState(""),[streaming,setStreaming]=useState(false),[selectedContainer,setSelectedContainer]=useState(""),
 [metrics,setMetrics]=useState(null),[metricsLoading,setMetricsLoading]=useState(false);
 const streamAbort=useRef(null);
 async function api(path){
  const r=await fetch(path);
  if(!r.ok){let d={};try{d=await r.json()}catch{}throw new Error(d.detail||`Request failed (${r.status})`)}
  return r.json();
 }
 function stopStream(){if(streamAbort.current){streamAbort.current.abort();streamAbort.current=null}setStreaming(false)}
 async function loadNamespaces(){
  try{
   const n=await api("/api/namespaces");setNamespaces(n);
   const requestedNamespace=new URLSearchParams(window.location.search).get("namespace");
   if(!ns&&n.length)setNs(n.some(item=>item.name===requestedNamespace)?requestedNamespace:n[0].name);
   else if(ns&&!n.some(x=>x.name===ns))setNs(n[0]?.name||"");
  }catch(e){setError(e.message)}
 }
 async function loadOverview(){
  try{setOverview(await api("/api/overview"))}
  catch(e){setError(`Cluster overview: ${e.message}`)}
 }
 async function loadPods(){
  if(!ns){setPods([]);return}
  try{setBusy(true);setError("");setPods(await api(`/api/pods?namespace=${encodeURIComponent(ns)}`))}
  catch(e){setError(e.message)}
  finally{setBusy(false)}
 }
 async function loadMetrics(){
  if(!ns){setMetrics({available:false,message:"Select an accessible namespace to view pod metrics.",items:[]});return}
  setMetricsLoading(true);
  try{setMetrics(await api(`/api/metrics/pods?namespace=${encodeURIComponent(ns)}`))}
  catch(e){setMetrics({available:false,message:e.message,items:[]})}
  finally{setMetricsLoading(false)}
 }
 useEffect(()=>{loadNamespaces();loadOverview()},[]);
 useEffect(()=>{loadPods();const t=setInterval(loadPods,30000);return()=>clearInterval(t)},[ns]);
 useEffect(()=>{loadMetrics();const t=setInterval(loadMetrics,30000);return()=>clearInterval(t)},[ns]);
 useEffect(()=>{const t=setInterval(loadOverview,30000);return()=>clearInterval(t)},[]);
 useEffect(()=>()=>{streamAbort.current?.abort()},[]);
 async function open(p,a){
  stopStream();setSelected(p);setAction(a);setPayload(null);setError("");setLogsText("");
  const defaultContainer=p.containers?.[0]||"";setSelectedContainer(defaultContainer);
  try{
   const q=`?namespace=${encodeURIComponent(ns)}`;
   let path=a==="Logs"?`/api/pods/${encodeURIComponent(p.name)}/logs${q}${defaultContainer?`&container=${encodeURIComponent(defaultContainer)}`:""}`:
    a==="ConfigMaps"?`/api/pods/${encodeURIComponent(p.name)}/configmaps${q}`:
    a==="Secrets"?`/api/pods/${encodeURIComponent(p.name)}/secrets${q}`:
    a==="Events"?`/api/pods/${encodeURIComponent(p.name)}/events${q}`:
    a==="Troubleshoot"?`/api/pods/${encodeURIComponent(p.name)}/diagnostics${q}`:
    `/api/pods/${encodeURIComponent(p.name)}/details${q}`;
   const result=await api(path);setPayload(result);if(a==="Logs")setLogsText(result.logs||"");
  }catch(e){setError(e.message)}
 }
 const deepLinkHandled=useRef(false);
 useEffect(()=>{
  if(deepLinkHandled.current||!ns||!pods.length)return;
  const params=new URLSearchParams(window.location.search);
  const targetPod=params.get("pod");
  if(!targetPod)return;
  const targetNamespace=params.get("namespace");
  if(targetNamespace&&targetNamespace!==ns)return;
  const pod=pods.find(item=>item.name===targetPod);
  if(!pod)return;
  deepLinkHandled.current=true;
  const requestedAction=params.get("action");
  open(pod,["Logs","Events","Troubleshoot"].includes(requestedAction)?requestedAction:"Troubleshoot");
 },[ns,pods]);
 async function startStream(){
  if(!selected||!ns||!selectedContainer)return;
  stopStream();
  const controller=new AbortController();streamAbort.current=controller;
  setStreaming(true);setError("");
  try{
   const q=new URLSearchParams({namespace:ns,container:selectedContainer,tail_lines:"100"});
   const response=await fetch(`/api/pods/${encodeURIComponent(selected.name)}/logs/stream?${q}`,{signal:controller.signal});
   if(!response.ok){let message=`Log stream failed (${response.status})`;try{const body=await response.json();message=body.detail||message}catch{}throw new Error(message)}
   if(!response.body)throw new Error("Streaming is not supported by this browser.");
   const reader=response.body.getReader(),decoder=new TextDecoder();
   while(true){const {value,done}=await reader.read();if(done)break;const chunk=decoder.decode(value,{stream:true});if(chunk)setLogsText(previous=>(previous+chunk).slice(-300000))}
  }catch(e){if(e.name!=="AbortError")setError(e.message)}
  finally{if(streamAbort.current===controller){streamAbort.current=null;setStreaming(false)}}
 }
 async function loadPreviousLogs(){
  if(!selected)return;stopStream();setError("");
  try{const q=new URLSearchParams({namespace:ns,previous:"true",container:selectedContainer});
   const result=await api(`/api/pods/${encodeURIComponent(selected.name)}/logs?${q}`);
   setPayload(result);setLogsText(result.logs||"");
  }catch(e){setError(e.message)}
 }
 const closeDrawer=()=>{stopStream();setSelected(null);setPayload(null)};
 const filtered=pods.filter(p=>p.name.toLowerCase().includes(search.toLowerCase()));
 const status=p=>p.reason||p.phase;
 const nodeSummary=overview?.nodes;
 const workloads=overview?.workloads;
 return <div className="app">
  <header><div className="brand"><Activity/><div><b>Mobile SRE</b><small>Kubernetes operations platform</small></div></div><span className="readonly">READ ONLY</span></header>
  <main>
   <section className="toolbar">
    <div><label>CLUSTER</label><b>Current Kubernetes context</b></div>
    <div><label>NAMESPACE</label><select value={ns} onChange={e=>setNs(e.target.value)}>{namespaces.map(n=><option key={n.name} value={n.name}>{n.name}</option>)}</select></div>
    <button className="refresh" onClick={()=>{loadNamespaces();loadOverview();loadPods()}}><RefreshCw size={16}/> Refresh</button>
   </section>

   <section className="section-title">
    <div><h2>Cluster overview</h2><p>Health summary across namespaces this dashboard is authorized to inspect.</p></div>
    <span className="scope"><Shield size={13}/> Scoped access</span>
   </section>
   <section className="overview-grid">
    <Metric label="Accessible namespaces" value={overview?.namespaces?.accessible} detail="RBAC-approved"/>
    <Metric label="Total pods" value={overview?.pods?.total} detail="Approved namespaces"/>
    <Metric label="Running pods" value={overview?.pods?.running} kind="good"/>
    <Metric label="Needs attention" value={overview?.pods?.needsAttention} kind={overview?.pods?.needsAttention>0?"warn":""}/>
   </section>
   <section className="overview-lower">
    <div className="panel node-panel">
     <div className="panel-head"><div><Server size={17}/><b>Node health</b></div>
      {nodeSummary?.available&&<span className={nodeSummary.notReady?"pill warn":"pill good"}>{nodeSummary.ready}/{nodeSummary.total} Ready</span>}
     </div>
     {!nodeSummary?<p className="muted">Loading node status…</p>:
      !nodeSummary.available?<p className="muted">{nodeSummary.message||"Node information unavailable."}</p>:
      nodeSummary.items.length?nodeSummary.items.map(node=><div className="node-row" key={node.name}>
       <div className="node-icon"><Server size={16}/></div>
       <div className="node-info"><b>{node.name}</b><small>{node.roles.length?node.roles.join(", "):"Worker"}{node.kubeletVersion?` · ${node.kubeletVersion}`:""}</small></div>
       <span className={node.ready?"node-state ready":"node-state notready"}>{node.ready?"Ready":"NotReady"}</span>
      </div>):<p className="muted">No nodes reported by the Kubernetes API.</p>}
    </div>
    <div className="panel workload-panel">
     <div className="panel-head"><div><Layers size={17}/><b>Workload health</b></div><span className="panel-caption">Authorized namespaces</span></div>
     {workloads?[
      ["Deployments",workloads.deployments],
      ["StatefulSets",workloads.statefulSets],
      ["DaemonSets",workloads.daemonSets]
     ].map(([label,item])=><div className="workload-row" key={label}>
      <div><b>{label}</b><small>{item.healthy} healthy · {item.unhealthy} unhealthy</small></div>
      <strong>{item.total}</strong>
     </div>):<p className="muted">Loading workload health…</p>}
    </div>
   </section>
   {overview&&<p className="updated">Updated {new Date(overview.generatedAt).toLocaleTimeString()} · Refreshes every 30 seconds · Node metadata is cluster-wide; pod and workload counts are RBAC-scoped.</p>}

   <section className="stats">
    <div><b>{pods.length}</b><small>Pods in selected namespace</small></div>
    <div><b>{pods.filter(p=>p.phase==="Running"&&!p.reason).length}</b><small>Running</small></div>
    <div><b>{pods.filter(p=>p.phase==="Pending").length}</b><small>Pending</small></div>
    <div><b>{pods.filter(p=>p.phase!=="Running"&&p.phase!=="Pending"||p.reason).length}</b><small>Needs attention</small></div>
   </section>
   <section className="listhead"><h2>Pods <small>{ns||"No accessible namespace"}</small></h2><div className="search"><Search size={16}/><input placeholder="Search pods" value={search} onChange={e=>setSearch(e.target.value)}/></div></section>
   {error&&<div className="error"><AlertTriangle size={17}/>{error}</div>}
   {!namespaces.length&&!error&&<p className="muted">No namespaces are currently accessible to this dashboard service account.</p>}
   <div className="pods">{filtered.map(p=>{
    const metric=metrics?.available?(metrics.items||[]).find(item=>item.name===p.name):null;
    const limits=Object.values(p.resourceLimits||{});
    const cpuLimit=limits.reduce((sum,item)=>sum+(item.cpuMillicores||0),0);
    const memoryLimit=limits.reduce((sum,item)=>sum+(item.memoryMiB||0),0);
    const cpuPercent=metric&&cpuLimit>0?Math.min(100,metric.cpuMillicores/cpuLimit*100):null;
    const memoryPercent=metric&&memoryLimit>0?Math.min(100,metric.memoryMiB/memoryLimit*100):null;
    return <article className="pod" key={p.name}>
    <div className="podtop" onClick={()=>open(p,"Overview")}><div><b>{p.name}</b><small>Restarts: {p.restarts} · {p.containers.length} container(s)</small></div><span className={`status ${status(p).toLowerCase().replaceAll("_","-")}`}>{status(p)}</span></div>
    <div className="pod-metrics">
     <div className="pod-metric"><div className="pod-metric-label"><small>CPU</small><b>{metric?metric.cpuMillicores+"m":"—"}</b></div>
      {cpuPercent!==null?<><div className="usage-track"><span className="usage-fill cpu-fill" style={{width:cpuPercent+"%"}}/></div><small className="usage-caption">{cpuPercent.toFixed(1)}% of configured limit</small></>:<small className="usage-caption">{metric?"No CPU limit configured":metrics?.available===false?"Metrics unavailable":"Waiting for metrics"}</small>}
     </div>
     <div className="pod-metric"><div className="pod-metric-label"><small>Memory</small><b>{metric?metric.memoryMiB+" MiB":"—"}</b></div>
      {memoryPercent!==null?<><div className="usage-track"><span className="usage-fill memory-fill" style={{width:memoryPercent+"%"}}/></div><small className="usage-caption">{memoryPercent.toFixed(1)}% of configured limit</small></>:<small className="usage-caption">{metric?"No memory limit configured":metrics?.available===false?"Metrics unavailable":"Waiting for metrics"}</small>}
     </div>
    </div>
    <div className="actions"><button onClick={()=>open(p,"Logs")}><FileText/>Logs</button><button onClick={()=>open(p,"Events")}><Clock3/>Events</button><button onClick={()=>open(p,"Troubleshoot")}><Stethoscope/>Diagnose</button><button onClick={()=>open(p,"ConfigMaps")}><Settings/>ConfigMaps</button><button onClick={()=>open(p,"Secrets")}><Shield/>Secrets</button><button className="details" onClick={()=>open(p,"Overview")}>Details <ChevronRight/></button></div>
   </article>})}</div>
   {busy&&<p className="muted">Refreshing pod inventory…</p>}
  </main>
  {selected&&<div className="overlay" onClick={closeDrawer}><aside className="drawer" onClick={e=>e.stopPropagation()}>
   <div className="drawerhead"><div><small>POD DETAILS</small><h2>{selected.name}</h2></div><button className="iconbtn" onClick={closeDrawer}><X/></button></div>
   <nav>{["Overview","Logs","Events","Troubleshoot","ConfigMaps","Secrets"].map(a=><button className={action===a?"active":""} onClick={()=>open(selected,a)} key={a}>{a}</button>)}</nav>
   <div className="content">{error&&<div className="error">{error}</div>}{!payload?<p className="muted">Loading…</p>:
    action==="Logs"?<><div className="log-toolbar"><label>Container<select value={selectedContainer} onChange={async e=>{const value=e.target.value;stopStream();setSelectedContainer(value);setLogsText("");setPayload(null);setError("");try{const q=new URLSearchParams({namespace:ns,container:value});const result=await api(`/api/pods/${encodeURIComponent(selected.name)}/logs?${q}`);setPayload(result);setLogsText(result.logs||"")}catch(err){setError(err.message)}}}>{(selected.containers||[]).map(name=><option key={name} value={name}>{name}</option>)}</select></label><div className="log-buttons">{!streaming?<button onClick={startStream}><Play size={14}/> Live</button>:<button onClick={stopStream}><Square size={14}/> Stop</button>}<button onClick={loadPreviousLogs}>Previous logs</button></div></div><div className="stream-state"><span className={streaming?"stream-dot active":"stream-dot"}/>{streaming?"Live stream connected":"Snapshot / stream stopped"} · Timestamped</div><pre className="log-output">{logsText||"No log output available. Pending pods may not have container logs yet; inspect Events and diagnostics."}</pre></>:
    action==="ConfigMaps"?payload.length?payload.map(x=><div className="resource" key={x.name}><b>{x.name}</b>{x.unavailable?<p>Configuration unavailable</p>:<pre>{JSON.stringify(x.data,null,2)}</pre>}</div>):<p className="muted">No ConfigMaps referenced by this pod.</p>:
    action==="Secrets"?payload.length?payload.map(x=><div className="resource" key={x.name}><b><Shield size={15}/> {x.name}</b><p>Reference: {x.reference} · Values are not retrieved.</p></div>):<p className="muted">No Secret references found in this pod specification.</p>:
    action==="Events"?payload.length?payload.map((x,i)=><div className={`event-card ${x.type==="Warning"?"warning":""}`} key={i}><div><b>{x.reason}</b><span>{x.type} · {x.count||1} occurrence(s)</span></div><p>{x.message}</p><small>{x.lastTimestamp||"Timestamp unavailable"}{x.source?` · ${x.source}`:""}</small></div>):<p className="muted">No retained events found for this pod.</p>:
    action==="Troubleshoot"?<><div className="diagnostic-summary"><Stethoscope size={18}/><div><b>{payload.phase} · {payload.name}</b><small>Node: {payload.node||"Not assigned"}</small></div></div>{(payload.findings||[]).map((f,i)=><div className={`finding ${f.severity}`} key={i}><div><span className="severity">{f.severity.toUpperCase()}</span><b>{f.title}</b></div><p>{f.message}</p><small><strong>Suggested checks:</strong> {f.recommendation}</small></div>)}<p className="muted">Guidance only. No cluster changes are performed.</p></>:
    <><div className="resource"><b>Status: {payload.phase||"Unknown"}</b><p>Node: {payload.node||"Not assigned"} · IP: {payload.podIP||"Not assigned"}</p></div><h3>Conditions</h3>{(payload.conditions||[]).map((c,i)=><div className="resource" key={i}><b>{c.type}: {c.status}</b><p>{c.reason} {c.message}</p></div>)}<h3>Containers</h3>{(payload.containers||[]).map(c=><div className="resource" key={c.name}><b>{c.name}</b><p>{c.image}</p></div>)}</>}
   </div>
  </aside></div>}
  <footer>Mobile SRE · Read-only Kubernetes diagnostics · Secret values are never retrieved</footer>
 </div>;
}
createRoot(document.getElementById("root")).render(<App/>);
