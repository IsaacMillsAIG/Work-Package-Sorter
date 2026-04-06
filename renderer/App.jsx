// ─── WORK PACKAGE SORTER — ELECTRON DESKTOP APP ───────────────────────────
// React via UMD globals (no bundler — works natively in Electron's renderer)

const { useState, useCallback, useEffect, useRef } = React;

// ── Theme ──────────────────────────────────────────────────────────────────
const T = {
  bg:        "#0f1117",
  card:      "#181b23",
  hover:     "#22262f",
  border:    "#2a2e38",
  text:      "#e8eaed",
  muted2:    "#9ca3b0",
  muted:     "#5f6775",
  accent:    "#3b82f6",
  accentGlow:"rgba(59,130,246,0.15)",
  success:   "#22c55e",
  warning:   "#f59e0b",
  danger:    "#ef4444",
};

const css = (obj) => Object.entries(obj).map(([k,v])=>`${k}:${v}`).join(";");

// ── Work package rules ────────────────────────────────────────────────────
const DEFAULT_RULES = [
  { id:"wp01", name:"WP-01 Small Parts & Embeds",      color:"#22c55e", desc:"Standalone angles, bent plates, misc small pieces",           rules:{ mark_prefix_in:["A","BP","M","PL"] } },
  { id:"wp02", name:"WP-02 Simple Beams (CNC only)",   color:"#3b82f6", desc:"Beams with no attached parts — straight off PythonX",         rules:{ details_of_in:["BEAM"], max_minor_marks:0, has_bent_plates:false, has_cjp_dcw:false } },
  { id:"wp03", name:"WP-03 Beams w/ Bent Plates",      color:"#f97316", desc:"Beams with bent plate attachments — press brake required",     rules:{ details_of_in:["BEAM"], has_bent_plates:true, has_cjp_dcw:false } },
  { id:"wp04", name:"WP-04 Moderate Beams",            color:"#8b5cf6", desc:"Beams with 1–4 parts, no bent plates, standard welding",       rules:{ details_of_in:["BEAM"], min_minor_marks:1, max_minor_marks:4, has_bent_plates:false, has_cjp_dcw:false } },
  { id:"wp05", name:"WP-05 Complex Beams",             color:"#f59e0b", desc:"Beams with 5+ parts, no bent plates, heavy fitting",           rules:{ details_of_in:["BEAM"], min_minor_marks:5, has_bent_plates:false, has_cjp_dcw:false } },
  { id:"wp06", name:"WP-06 Beams w/ CJP/DCW",         color:"#ef4444", desc:"Beams requiring CJP or demand critical welds",                 rules:{ details_of_in:["BEAM"], has_cjp_dcw:true } },
  { id:"wp07", name:"WP-07 Simple Columns",            color:"#06b6d4", desc:"Columns without CJP welds",                                   rules:{ details_of_in:["COLUMN"], has_cjp_dcw:false } },
  { id:"wp08", name:"WP-08 Complex Columns (CJP/DCW)", color:"#dc2626", desc:"Columns with demand critical welds — UT required",             rules:{ details_of_in:["COLUMN"], has_cjp_dcw:true } },
  { id:"wp09", name:"WP-09 Vertical Braces",           color:"#14b8a6", desc:"Bracing members (VB/BR series)",                               rules:{ mark_prefix_in:["VB","BR"] } },
  { id:"wp99", name:"WP-99 Unclassified",              color:"#6b7280", desc:"Review manually",                                              rules:{} },
];

function matchesRule(d, rules) {
  if (!rules || Object.keys(rules).length === 0) return true;
  for (const [k, v] of Object.entries(rules)) {
    if (k === "mark_prefix_in"   && !v.includes(d.mark_prefix))    return false;
    if (k === "details_of_in"    && !v.includes(d.details_of))     return false;
    if (k === "min_minor_marks"  && d.num_minor_marks < v)         return false;
    if (k === "max_minor_marks"  && d.num_minor_marks > v)         return false;
    if (k === "has_bent_plates"  && d.has_bent_plates  !== v)      return false;
    if (k === "has_cjp_dcw"     && d.has_cjp_dcw      !== v)      return false;
    if (k === "fab_status"       && d.fab_status        !== v)      return false;
    if (k === "galvanize"        && d.galvanize         !== v)      return false;
  }
  return true;
}

function classifyDrawings(drawings, rules) {
  return drawings.map((d) => {
    for (const wp of rules) {
      if (matchesRule(d, wp.rules)) return { ...d, work_package: wp.name, wp_color: wp.color };
    }
    return { ...d, work_package:"WP-99 Unclassified", wp_color:"#6b7280" };
  });
}

// ── CSV generator ─────────────────────────────────────────────────────────
function generateCsv(drawings) {
  const headers = ["sheet_no","mark_prefix","details_of","main_section","total_weight",
    "num_minor_marks","has_bent_plates","has_cjp_dcw","has_pjp","fab_status",
    "galvanize","erection_dwg_ref","grade","work_package","page_number"];
  const rows = [headers.join(",")];
  for (const d of [...drawings].sort((a,b)=>a.sheet_no.localeCompare(b.sheet_no))) {
    rows.push(headers.map(h => {
      const v = d[h] === undefined ? "" : d[h];
      const s = String(v).replace(/"/g,'""');
      return s.includes(",") || s.includes('"') ? `"${s}"` : s;
    }).join(","));
  }
  return rows.join("\r\n");
}

// ── Donut chart (D3) ──────────────────────────────────────────────────────
function DonutChart({ data, width=260, height=260 }) {
  const ref = useRef();
  useEffect(() => {
    if (!data?.length) return;
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    const r = Math.min(width, height) / 2 - 10;
    const g = svg.append("g").attr("transform", `translate(${width/2},${height/2})`);
    const pie = d3.pie().value(d=>d.count).sort(null).padAngle(0.02);
    const arc     = d3.arc().innerRadius(r*0.55).outerRadius(r);
    const arcHov  = d3.arc().innerRadius(r*0.55).outerRadius(r+6);
    g.selectAll(".a").data(pie(data)).enter().append("path")
      .attr("d", arc).attr("fill", d=>d.data.color)
      .attr("stroke","rgba(0,0,0,0.15)").attr("stroke-width",1)
      .style("cursor","pointer")
      .on("mouseenter", function(){ d3.select(this).transition().duration(120).attr("d",arcHov); })
      .on("mouseleave", function(){ d3.select(this).transition().duration(120).attr("d",arc); });
    const total = data.reduce((a,b)=>a+b.count,0);
    g.append("text").attr("text-anchor","middle").attr("dy","-0.2em")
      .style("font-size","28px").style("font-weight","700").style("fill",T.text).text(total);
    g.append("text").attr("text-anchor","middle").attr("dy","1.3em")
      .style("font-size","11px").style("fill",T.muted).style("text-transform","uppercase")
      .style("letter-spacing","0.08em").text("drawings");
  }, [data]);
  return React.createElement("svg", { ref, width, height });
}

// ── Horizontal bar chart (D3) ─────────────────────────────────────────────
function HBar({ data, width=460, height=240 }) {
  const ref = useRef();
  useEffect(() => {
    if (!data?.length) return;
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    const m = { top:8, right:50, bottom:8, left:170 };
    const w = width - m.left - m.right;
    const h = height - m.top - m.bottom;
    const g = svg.append("g").attr("transform",`translate(${m.left},${m.top})`);
    const y = d3.scaleBand().domain(data.map(d=>d.name)).range([0,h]).padding(0.35);
    const x = d3.scaleLinear().domain([0, d3.max(data,d=>d.count)]).range([0,w]);
    g.selectAll(".bar").data(data).enter().append("rect")
      .attr("y",d=>y(d.name)).attr("height",y.bandwidth())
      .attr("x",0).attr("width",d=>x(d.count))
      .attr("fill",d=>d.color).attr("rx",3);
    g.selectAll(".lbl").data(data).enter().append("text")
      .attr("y",d=>y(d.name)+y.bandwidth()/2).attr("x",-8)
      .attr("text-anchor","end").attr("dominant-baseline","central")
      .style("font-size","11px").style("fill",T.muted2)
      .text(d=>d.name.replace(/^WP-\d+\s*/,""));
    g.selectAll(".cnt").data(data).enter().append("text")
      .attr("y",d=>y(d.name)+y.bandwidth()/2).attr("x",d=>x(d.count)+6)
      .attr("dominant-baseline","central")
      .style("font-size","12px").style("font-weight","600").style("fill",T.text)
      .text(d=>d.count);
  }, [data]);
  return React.createElement("svg", { ref, width, height });
}

// ── Error banner ──────────────────────────────────────────────────────────
function ErrorBanner({ message, onDismiss }) {
  if (!message) return null;
  return React.createElement("div", {
    style:{ background:"#2d1a1a", border:`1px solid ${T.danger}44`, borderRadius:10,
      padding:"14px 18px", marginBottom:20, display:"flex", alignItems:"flex-start", gap:12 }
  },
    React.createElement("span", { style:{ fontSize:18, flexShrink:0 } }, "⚠️"),
    React.createElement("div", { style:{ flex:1 } },
      React.createElement("div", { style:{ fontWeight:600, color:T.danger, marginBottom:4 } }, "Error"),
      React.createElement("pre", {
        style:{ fontSize:12, color:T.muted2, whiteSpace:"pre-wrap", fontFamily:"'JetBrains Mono',monospace", margin:0 }
      }, message)
    ),
    React.createElement("button", {
      onClick:onDismiss,
      style:{ background:"none", border:"none", color:T.muted, cursor:"pointer", fontSize:18, padding:"0 4px" }
    }, "×")
  );
}

// ── Status bar ───────────────────────────────────────────────────────────
// Parses raw stderr lines from Python into a friendly phase + label
function parseStatus(lines) {
  if (!lines.length) return { phase: "starting", label: "Starting…", pct: 0 };
  // Walk backwards to find the most recent meaningful line
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i].trim();
    if (!line) continue;

    // Progress line format: "  43%  [145/338]  B1145"
    const barMatch = line.match(/(\d+)%\s+\[(\d+)\/(\d+)\]\s*(.*)/);
    if (barMatch) {
      const pct   = parseInt(barMatch[1]);
      const cur   = parseInt(barMatch[2]);
      const total = parseInt(barMatch[3]);
      const lbl   = barMatch[4].trim();
      return { pct, label: lbl || `${cur} / ${total}`, cur, total };
    }

    if (line.includes("Parsing")) return { phase:"parsing",    label:"Reading PDF pages…",      pct:0  };
    if (line.includes("Classifying")) return { phase:"classifying", label:"Classifying drawings…", pct:66 };
    if (line.includes("Splitting")) return { phase:"splitting",  label:"Writing output PDFs…",    pct:88 };
    if (line.includes("Summary CSV")) return { phase:"csv",      label:"Writing CSV summary…",    pct:96 };
    if (line.includes("Done"))       return { phase:"done",      label:"Complete",                pct:100};
  }
  return { phase:"running", label:"Running…", pct: 0 };
}

function StatusBar({ lines, visible }) {
  if (!visible) return null;
  const { pct, label, cur, total } = parseStatus(lines);
  const pctDisplay = Math.max(pct || 0, 2); // always show at least a sliver

  // Determine phase label shown on left
  let phaseLabel = "Parsing";
  if ((lines.some(l => l.includes("Classifying")))) phaseLabel = "Classifying";
  if ((lines.some(l => l.includes("Splitting"))))   phaseLabel = "Splitting PDFs";
  if ((lines.some(l => l.includes("Done"))))        phaseLabel = "Done";

  // Pulse animation keyframe injected once
  if (!document.getElementById("pulse-style")) {
    const s = document.createElement("style");
    s.id = "pulse-style";
    s.textContent = `@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.5} }`;
    document.head.appendChild(s);
  }

  const isDone = phaseLabel === "Done";

  return React.createElement("div", {
    style:{ marginBottom:20 }
  },
    // Phase row
    React.createElement("div", {
      style:{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:8 }
    },
      React.createElement("div", { style:{ display:"flex", alignItems:"center", gap:8 } },
        // Animated dot
        React.createElement("div", {
          style:{
            width:8, height:8, borderRadius:"50%",
            background: isDone ? T.success : T.accent,
            animation: isDone ? "none" : "pulse 1.2s ease-in-out infinite",
            flexShrink:0,
          }
        }),
        React.createElement("span", {
          style:{ fontSize:13, fontWeight:600, color:T.text }
        }, phaseLabel)
      ),
      // Current item label (sheet number etc.)
      label && React.createElement("span", {
        style:{ fontSize:11, color:T.muted, fontFamily:"'JetBrains Mono',monospace",
          maxWidth:260, overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }
      }, label),
      // Percentage
      React.createElement("span", {
        style:{ fontSize:12, fontWeight:700, color: isDone ? T.success : T.accent,
          fontFamily:"'JetBrains Mono',monospace", minWidth:36, textAlign:"right" }
      }, `${pct || 0}%`)
    ),

    // Progress track
    React.createElement("div", {
      style:{ height:6, background:T.border, borderRadius:99, overflow:"hidden" }
    },
      React.createElement("div", {
        style:{
          height:"100%", borderRadius:99,
          width:`${pctDisplay}%`,
          background: isDone
            ? T.success
            : `linear-gradient(90deg, ${T.accent}, #8b5cf6)`,
          transition:"width 0.4s ease, background 0.3s",
          boxShadow: isDone ? `0 0 8px ${T.success}66` : `0 0 8px ${T.accent}66`,
        }
      })
    ),

    // Page count sub-label
    cur && total && React.createElement("div", {
      style:{ fontSize:10, color:T.muted, marginTop:5, textAlign:"right",
        fontFamily:"'JetBrains Mono',monospace" }
    }, `${cur} / ${total} pages`)
  );
}

// ── Drag-and-drop zone ────────────────────────────────────────────────────
function DropZone({ onFile, disabled }) {
  const [drag, setDrag] = useState(false);

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDrag(false);
    if (disabled) return;
    const file = e.dataTransfer.files[0];
    if (file && file.name.endsWith(".pdf")) onFile(file.path);
  }, [disabled, onFile]);

  const handlePick = async () => {
    if (disabled) return;
    const p = await window.electronAPI.pickPdf();
    if (p) onFile(p);
  };

  return React.createElement("div", {
    onDragOver: e => { e.preventDefault(); if (!disabled) setDrag(true); },
    onDragLeave: () => setDrag(false),
    onDrop: handleDrop,
    onClick: handlePick,
    style:{
      border: `2px dashed ${drag ? T.accent : T.border}`,
      borderRadius:12, padding:"40px 32px", textAlign:"center",
      background: drag ? T.accentGlow : T.card,
      cursor: disabled ? "not-allowed" : "pointer",
      transition:"all 0.15s", opacity: disabled ? 0.5 : 1,
    }
  },
    React.createElement("div", { style:{fontSize:36, marginBottom:10} }, "📄"),
    React.createElement("div", { style:{fontWeight:600, fontSize:15, marginBottom:6} },
      drag ? "Drop it!" : "Drag & drop a PDF here"
    ),
    React.createElement("div", { style:{color:T.muted, fontSize:13} },
      "or click to browse"
    )
  );
}

// ── Drawing table ─────────────────────────────────────────────────────────
function DrawingTable({ drawings, filter }) {
  const rows = filter === "ALL" ? drawings : drawings.filter(d=>d.work_package===filter);
  const th = (label) => React.createElement("th", {
    style:{ padding:"10px 12px", textAlign:"left", borderBottom:`2px solid ${T.border}`,
      color:T.muted, fontWeight:600, fontSize:10, textTransform:"uppercase",
      letterSpacing:"0.05em", whiteSpace:"nowrap", background:T.card, position:"sticky", top:0 }
  }, label);
  const td = (val, extra={}) => React.createElement("td", {
    style:{ padding:"8px 12px", color:T.muted2, ...extra }
  }, val);

  return React.createElement("div", {
    style:{ overflowX:"auto", border:`1px solid ${T.border}`, borderRadius:8,
      maxHeight:440, overflowY:"auto" }
  },
    React.createElement("table", { style:{width:"100%", borderCollapse:"collapse", fontSize:12} },
      React.createElement("thead", null,
        React.createElement("tr", null,
          ...(["Sheet","Type","Section","Parts","Bent PL","CJP","Status","Erec Ref","Weight","Work Package"]).map(th)
        )
      ),
      React.createElement("tbody", null,
        rows.map((d,i) => React.createElement("tr", {
          key: d.sheet_no+i,
          style:{ borderBottom:`1px solid ${T.border}`, background: i%2===0?"transparent":T.card }
        },
          td(d.sheet_no, { fontWeight:600, fontFamily:"monospace", color:T.text }),
          td(d.details_of),
          td(d.main_section, { fontFamily:"monospace", fontSize:11 }),
          td(d.num_minor_marks, { textAlign:"center" }),
          React.createElement("td", { style:{padding:"8px 12px", textAlign:"center"} },
            d.has_bent_plates && React.createElement("span", { style:{color:T.warning,fontWeight:700} }, "●")
          ),
          React.createElement("td", { style:{padding:"8px 12px", textAlign:"center"} },
            d.has_cjp_dcw && React.createElement("span", { style:{color:T.danger,fontWeight:700} }, "●")
          ),
          React.createElement("td", { style:{padding:"8px 12px"} },
            React.createElement("span", {
              style:{ display:"inline-block", padding:"2px 8px", borderRadius:10,
                fontSize:10, fontWeight:600,
                background: d.fab_status==="HOLD"?"#fef3c7":"#dcfce7",
                color: d.fab_status==="HOLD"?"#92400e":"#166534" }
            }, d.fab_status==="FOR FABRICATION"?"FOR FAB":d.fab_status)
          ),
          td(d.erection_dwg_ref, { fontFamily:"monospace", fontSize:11, color:T.muted }),
          React.createElement("td", { style:{padding:"8px 12px", textAlign:"right", fontFamily:"monospace", fontSize:11} },
            d.total_weight > 0
              ? `${d.total_weight} lbs`
              : React.createElement("span", { style:{color:T.warning}, title:"Weight not found in PDF" }, "⚠ —")
          ),
          React.createElement("td", { style:{padding:"8px 12px"} },
            React.createElement("span", {
              style:{ display:"inline-flex", alignItems:"center", gap:6,
                padding:"2px 8px", borderRadius:10,
                background: d.wp_color+"18", color:d.wp_color,
                fontSize:10, fontWeight:700, whiteSpace:"nowrap" }
            },
              React.createElement("span", { style:{width:6,height:6,borderRadius:2,background:d.wp_color} }),
              d.work_package.replace(/^WP-\d+\s*/,"")
            )
          )
        ))
      )
    )
  );
}

// ── Available condition types ─────────────────────────────────────────────
const CONDITION_TYPES = [
  { key:"mark_prefix_in",  label:"Mark prefix is one of",     type:"csv",    hint:"e.g. A, BP, M" },
  { key:"details_of_in",   label:"Member type is one of",     type:"csv",    hint:"e.g. BEAM, COLUMN" },
  { key:"min_minor_marks", label:"Min attached parts",         type:"number", hint:"e.g. 1" },
  { key:"max_minor_marks", label:"Max attached parts",         type:"number", hint:"e.g. 4" },
  { key:"has_bent_plates", label:"Has bent plates",            type:"bool",   hint:"" },
  { key:"has_cjp_dcw",     label:"Has CJP / DCW weld",        type:"bool",   hint:"" },
  { key:"has_pjp",         label:"Has PJP weld",               type:"bool",   hint:"" },
  { key:"has_moment_connection", label:"Has moment connection", type:"bool",  hint:"" },
  { key:"galvanize",       label:"Galvanized",                 type:"bool",   hint:"" },
  { key:"fab_status",      label:"Fab status equals",          type:"text",   hint:"FOR FABRICATION or HOLD" },
];

const COLORS = ["#22c55e","#3b82f6","#f97316","#8b5cf6","#f59e0b","#ef4444",
                 "#06b6d4","#dc2626","#14b8a6","#6b7280","#a855f7","#ec4899","#84cc16"];

// ── Rule editor ───────────────────────────────────────────────────────────
function RuleEditor({ rule, onChange, onDelete, index, locked }) {
  const [open, setOpen]           = useState(false);
  const [addingCond, setAddingCond] = useState(false);
  const [newCondKey, setNewCondKey] = useState(CONDITION_TYPES[0].key);
  const [newCondVal, setNewCondVal] = useState("");

  const updateMeta  = (field, val) => { if (locked) return; onChange(index, { ...rule, [field]: val }); };
  const updateCond  = (field, val) => { if (locked) return; onChange(index, { ...rule, rules:{ ...rule.rules, [field]:val } }); };
  const removeCond  = (field) => { if (locked) return; const r={...rule.rules}; delete r[field]; onChange(index,{...rule,rules:r}); };

  const inputStyle = {
    background: locked ? T.hover : T.bg,
    border:`1px solid ${T.border}`, borderRadius:6,
    color: locked ? T.muted : T.text,
    fontSize:12, padding:"6px 10px", outline:"none",
    fontFamily:"'DM Sans','Segoe UI',system-ui,sans-serif",
    pointerEvents: locked ? "none" : "auto",
    opacity: locked ? 0.6 : 1,
  };

  // Parse typed value based on condition type
  const parseVal = (key, raw) => {
    const def = CONDITION_TYPES.find(c=>c.key===key);
    if (!def) return raw;
    if (def.type === "csv")    return raw.split(",").map(s=>s.trim()).filter(Boolean);
    if (def.type === "number") return parseInt(raw) || 0;
    if (def.type === "bool")   return raw === "true";
    return raw;
  };

  const handleAddCond = () => {
    if (!newCondVal.trim() && CONDITION_TYPES.find(c=>c.key===newCondKey)?.type !== "bool") return;
    const val = parseVal(newCondKey, newCondVal || "false");
    updateCond(newCondKey, val);
    setNewCondVal("");
    setAddingCond(false);
  };

  const condDef = CONDITION_TYPES.find(c=>c.key===newCondKey) || CONDITION_TYPES[0];

  return React.createElement("div", {
    style:{ background:T.card, border:`1px solid ${T.border}`,
      borderLeft:`4px solid ${rule.color}`, borderRadius:8, marginBottom:8, overflow:"hidden" }
  },
    // ── Header row ──────────────────────────────────────────────────
    React.createElement("div", {
      style:{ display:"flex", alignItems:"center", gap:10, cursor: locked ? "default" : "pointer", padding:"12px 16px",
        opacity: locked ? 0.75 : 1 },
      onClick:()=>{ if (!locked) setOpen(!open); }
    },
      React.createElement("span", { style:{width:12,height:12,borderRadius:3,background:rule.color,flexShrink:0} }),
      React.createElement("span", { style:{fontWeight:600,fontSize:13,color:T.text,flex:1} }, rule.name),
      locked
        ? React.createElement("span", { style:{fontSize:11,color:T.muted} }, "🔒 locked")
        : React.createElement("span", { style:{fontSize:11,color:T.muted} },
            `${Object.keys(rule.rules).length} condition${Object.keys(rule.rules).length!==1?"s":""}`),
      !locked && React.createElement("span", {
        style:{fontSize:16,color:T.muted,transform:open?"rotate(180deg)":"none",transition:"0.2s"}
      }, "▾")
    ),

    // ── Expanded body ────────────────────────────────────────────────
    open && React.createElement("div", {
      style:{padding:"0 16px 16px", borderTop:`1px solid ${T.border}`}
    },

      // ── Name field ──────────────────────────────────────────────
      React.createElement("div", { style:{marginTop:12, display:"flex", gap:8, alignItems:"center"} },
        React.createElement("label", { style:{fontSize:11,color:T.muted,minWidth:80} }, "NAME"),
        React.createElement("input", {
          value: rule.name,
          onChange: e => updateMeta("name", e.target.value),
          style:{ ...inputStyle, flex:1 }
        })
      ),

      // ── Description field ────────────────────────────────────────
      React.createElement("div", { style:{marginTop:8, display:"flex", gap:8, alignItems:"center"} },
        React.createElement("label", { style:{fontSize:11,color:T.muted,minWidth:80} }, "DESC"),
        React.createElement("input", {
          value: rule.desc,
          onChange: e => updateMeta("desc", e.target.value),
          style:{ ...inputStyle, flex:1 }
        })
      ),

      // ── Color picker ─────────────────────────────────────────────
      React.createElement("div", { style:{marginTop:8, display:"flex", gap:8, alignItems:"center"} },
        React.createElement("label", { style:{fontSize:11,color:T.muted,minWidth:80} }, "COLOR"),
        React.createElement("div", { style:{display:"flex",gap:6,flexWrap:"wrap"} },
          COLORS.map(c => React.createElement("div", {
            key:c,
            onClick: () => updateMeta("color", c),
            style:{
              width:20, height:20, borderRadius:4, background:c, cursor:"pointer",
              border: rule.color===c ? `2px solid ${T.text}` : `2px solid transparent`,
              transition:"0.1s",
            }
          }))
        )
      ),

      // ── Conditions ───────────────────────────────────────────────
      React.createElement("div", { style:{marginTop:14, marginBottom:6, fontSize:11, color:T.muted, fontWeight:600, textTransform:"uppercase", letterSpacing:"0.05em"} },
        "Conditions"
      ),

      Object.keys(rule.rules).length === 0
        ? React.createElement("div", { style:{fontSize:12,color:T.muted,marginBottom:8,fontStyle:"italic"} },
            "No conditions — this rule matches everything (catch-all).")
        : Object.entries(rule.rules).map(([k,v]) => {
            const def = CONDITION_TYPES.find(c=>c.key===k);
            return React.createElement("div", {
              key:k,
              style:{ display:"flex",alignItems:"center",gap:8,padding:"6px 10px",marginBottom:4,
                background:T.bg,borderRadius:6,fontSize:12 }
            },
              React.createElement("span", { style:{color:T.muted2,fontWeight:600,flex:1} },
                def ? def.label : k),
              // Inline value editor
              def && def.type === "bool"
                ? React.createElement("select", {
                    value: String(v),
                    onChange: e => updateCond(k, e.target.value === "true"),
                    style:{ ...inputStyle, width:90 }
                  },
                    React.createElement("option", { value:"true" }, "Yes"),
                    React.createElement("option", { value:"false" }, "No")
                  )
                : def && def.type === "number"
                ? React.createElement("input", {
                    type:"number", value:v,
                    onChange: e => updateCond(k, parseInt(e.target.value)||0),
                    style:{ ...inputStyle, width:70, textAlign:"center" }
                  })
                : React.createElement("input", {
                    value: Array.isArray(v) ? v.join(", ") : String(v),
                    onChange: e => updateCond(k, parseVal(k, e.target.value)),
                    style:{ ...inputStyle, flex:2 },
                    placeholder: def ? def.hint : ""
                  }),
              React.createElement("button", {
                onClick: ()=>removeCond(k),
                style:{background:"none",border:"none",color:T.muted,cursor:"pointer",
                  fontSize:16,padding:"2px 6px",flexShrink:0}
              }, "×")
            );
          }),

      // ── Add condition row ────────────────────────────────────────
      !addingCond && !locked
        ? React.createElement("button", {
            onClick:()=>setAddingCond(true),
            style:{ marginTop:4, padding:"5px 12px", fontSize:11, borderRadius:6,
              background:"none", border:`1px dashed ${T.border}`, color:T.muted, cursor:"pointer" }
          }, "+ Add Condition")
        : React.createElement("div", {
            style:{ marginTop:8, padding:"10px 12px", background:T.bg,
              borderRadius:8, border:`1px solid ${T.border}` }
          },
            // Condition type selector
            React.createElement("div", { style:{display:"flex",gap:8,marginBottom:8,alignItems:"center"} },
              React.createElement("label", { style:{fontSize:11,color:T.muted,minWidth:60} }, "FIELD"),
              React.createElement("select", {
                value: newCondKey,
                onChange: e => { setNewCondKey(e.target.value); setNewCondVal(""); },
                style:{ ...inputStyle, flex:1 }
              },
                CONDITION_TYPES
                  .filter(c => !(c.key in rule.rules)) // hide already-added
                  .map(c => React.createElement("option", { key:c.key, value:c.key }, c.label))
              )
            ),
            // Value input (varies by type)
            condDef.type !== "bool" && React.createElement("div", {
              style:{display:"flex",gap:8,marginBottom:8,alignItems:"center"}
            },
              React.createElement("label", { style:{fontSize:11,color:T.muted,minWidth:60} }, "VALUE"),
              condDef.type === "number"
                ? React.createElement("input", {
                    type:"number", value:newCondVal, placeholder:"e.g. 4",
                    onChange:e=>setNewCondVal(e.target.value),
                    style:{ ...inputStyle, width:100 }
                  })
                : React.createElement("input", {
                    value:newCondVal, placeholder:condDef.hint,
                    onChange:e=>setNewCondVal(e.target.value),
                    style:{ ...inputStyle, flex:1 }
                  })
            ),
            condDef.type === "bool" && React.createElement("div", {
              style:{display:"flex",gap:8,marginBottom:8,alignItems:"center"}
            },
              React.createElement("label", { style:{fontSize:11,color:T.muted,minWidth:60} }, "VALUE"),
              React.createElement("select", {
                value:newCondVal||"true", onChange:e=>setNewCondVal(e.target.value),
                style:{ ...inputStyle, width:100 }
              },
                React.createElement("option", { value:"true" }, "Yes"),
                React.createElement("option", { value:"false" }, "No")
              )
            ),
            React.createElement("div", { style:{display:"flex",gap:8} },
              React.createElement("button", {
                onClick:handleAddCond,
                style:{ padding:"5px 14px", fontSize:11, borderRadius:6,
                  background:T.accent, border:"none", color:"#fff", cursor:"pointer", fontWeight:600 }
              }, "Add"),
              React.createElement("button", {
                onClick:()=>{ setAddingCond(false); setNewCondVal(""); },
                style:{ padding:"5px 14px", fontSize:11, borderRadius:6,
                  background:"none", border:`1px solid ${T.border}`, color:T.muted, cursor:"pointer" }
              }, "Cancel")
            )
          ),

      // ── Delete rule ──────────────────────────────────────────────
      rule.id !== "wp99" && !locked && React.createElement("button", {
        onClick:()=>onDelete(index),
        style:{ marginTop:14, padding:"5px 12px", fontSize:11, borderRadius:6,
          background:"none", border:`1px solid ${T.danger}44`, color:T.danger, cursor:"pointer" }
      }, "Delete Rule")
    )
  );
}

// ── Btn helper ────────────────────────────────────────────────────────────
function Btn({ onClick, children, variant="primary", disabled=false, small=false }) {
  const base = {
    padding: small ? "6px 14px" : "9px 22px",
    borderRadius:8, border:"none", fontWeight:600,
    fontSize: small ? 12 : 13, cursor: disabled ? "not-allowed" : "pointer",
    transition:"0.15s", opacity: disabled ? 0.5 : 1,
  };
  const styles = {
    primary: { background:`linear-gradient(135deg, ${T.accent}, #8b5cf6)`, color:"#fff", boxShadow:"0 4px 20px rgba(59,130,246,0.3)" },
    ghost:   { background:T.card, color:T.muted2, border:`1px solid ${T.border}` },
    success: { background:T.success+"22", color:T.success, border:`1px solid ${T.success}44` },
  };
  return React.createElement("button", { onClick: disabled?undefined:onClick, style:{ ...base, ...styles[variant] } }, children);
}

// ── MAIN APP ──────────────────────────────────────────────────────────────
function App() {
  const [rules, setRulesState]      = useState(DEFAULT_RULES);
  const [pdfPath, setPdfPath]       = useState("");
  const [outputDir, setOutputDir]   = useState("");
  const [drawings, setDrawings]     = useState([]);
  const [classified, setClassified] = useState([]);
  const [running, setRunning]       = useState(false);
  const [progressLines, setProgressLines] = useState([]);
  const [showProgress, setShowProgress]   = useState(false);
  const [error, setError]           = useState("");
  const [activeTab, setActiveTab]   = useState("overview");
  const [tableFilter, setTableFilter] = useState("ALL");
  const [exportMsg, setExportMsg]   = useState("");
  const [rulesOpen, setRulesOpen]   = useState(false);
  const [rulesSaved, setRulesSaved] = useState(false);

  // Wrap setRules to also auto-save to disk
  const setRules = (next) => {
    setRulesState(next);
    window.electronAPI.saveRules(next).then(ok => {
      if (ok) { setRulesSaved(true); setTimeout(()=>setRulesSaved(false), 1800); }
    });
  };

  // Load saved rules from disk on startup
  useEffect(() => {
    window.electronAPI.loadRules().then(saved => {
      if (saved && saved.length > 0) setRulesState(saved);
    });
  }, []);

  // Re-classify when rules or raw drawings change
  useEffect(() => {
    if (drawings.length > 0) setClassified(classifyDrawings(drawings, rules));
  }, [drawings, rules]);

  // Cancel support — we kill the Python process via a flag checked by main
  const cancelRef = useRef(false);
  const handleCancel = () => {
    cancelRef.current = true;
    window.electronAPI.cancelSorter();
  };

  // Use a ref to always append lines without stale closure issues
  const progressLinesRef = useRef([]);
  useEffect(() => {
    window.electronAPI.removeProgressListeners();
    window.electronAPI.onProgress((line) => {
      progressLinesRef.current = [...progressLinesRef.current, line];
      setProgressLines([...progressLinesRef.current]);
    });
    return () => window.electronAPI.removeProgressListeners();
  }, []);

  // ── Run sorter ────────────────────────────────────────────────────────
  const handleRun = async () => {
    if (!pdfPath) { setError("Please select a PDF file first."); return; }
    setError("");
    cancelRef.current = false;  // always reset before a new run
    progressLinesRef.current = [];
    setProgressLines([]);
    setShowProgress(true);
    setRunning(true);

    const dir = outputDir || (pdfPath.replace(/[^/\\]+$/, "") + "output");
    const result = await window.electronAPI.runSorter({ pdfPath, outputDir: dir });
    setRunning(false);

    if (cancelRef.current) {
      setShowProgress(false);
      return;
    }

    if (!result.success) {
      setError(result.error);
      return;
    }

    // Load drawings from JSON result
    // Patch total_weight onto each drawing from the wp_summary if the drawing-level value is 0
    // (the wp_summary totals are always correct from Python)
    const allDrawings = (result.data.drawings || []).map(d => ({
      ...d,
      total_weight: typeof d.total_weight === "number" ? d.total_weight : 0,
    }));
    setDrawings(allDrawings);
    setOutputDir(result.outputDir);
    setShowProgress(false);
    setActiveTab("overview");
  };

  // ── Export CSV ────────────────────────────────────────────────────────
  const handleExportCsv = async () => {
    if (!classified.length) return;
    const csv = generateCsv(classified);
    const result = await window.electronAPI.saveCsv(csv);
    if (result.success) {
      setExportMsg(`✓ Saved to ${result.filePath}`);
      setTimeout(()=>setExportMsg(""),4000);
    } else if (result.reason === "cancelled") {
      // user cancelled — no message needed
    } else {
      setError(result.message || "Export failed.");
    }
  };

  // ── Open output folder ────────────────────────────────────────────────
  const handleOpenFolder = () => {
    if (outputDir) window.electronAPI.openFolder(outputDir);
  };

  // ── WP summary ────────────────────────────────────────────────────────
  const wpSummary = {};
  classified.forEach(d => {
    if (!wpSummary[d.work_package]) {
      wpSummary[d.work_package] = { name:d.work_package, count:0, color:d.wp_color, weight:0, hold:0, forFab:0 };
    }
    wpSummary[d.work_package].count++;
    wpSummary[d.work_package].weight += d.total_weight||0;
    if (d.fab_status==="HOLD") wpSummary[d.work_package].hold++;
    else wpSummary[d.work_package].forFab++;
  });
  const wpList = Object.values(wpSummary).sort((a,b)=>a.name.localeCompare(b.name));
  const totalWeight = classified.reduce((a,d)=>a+(d.total_weight||0),0);
  const holdCount   = classified.filter(d=>d.fab_status==="HOLD").length;

  const hasData = classified.length > 0;

  // ── Render ────────────────────────────────────────────────────────────
  return React.createElement("div", {
    style:{ fontFamily:"'DM Sans','Segoe UI',system-ui,sans-serif",
      background:T.bg, color:T.text, height:"100vh",
      display:"flex", flexDirection:"column", overflow:"hidden" }
  },

    // ── Header ──────────────────────────────────────────────────────────
    React.createElement("div", {
      style:{ background:`linear-gradient(135deg,${T.bg},#1a1d28)`,
        borderBottom:`1px solid ${T.border}`, padding:"20px 28px",
        display:"flex", alignItems:"center", justifyContent:"space-between", flexShrink:0 }
    },
      React.createElement("div", { style:{display:"flex",alignItems:"center",gap:14} },
        React.createElement("div", {
          style:{ width:36,height:36,borderRadius:8,
            background:"linear-gradient(135deg,#3b82f6,#8b5cf6)",
            display:"flex",alignItems:"center",justifyContent:"center",fontSize:18,color:"#fff" }
        }, "⚙"),
        React.createElement("div", null,
          React.createElement("h1", { style:{fontSize:20,fontWeight:700,margin:0,letterSpacing:"-0.02em"} },
            "Work Package Sorter"),
          React.createElement("p", { style:{fontSize:11,color:T.muted,margin:0} },
            "Steel Fabrication Drawing Sequencer")
        )
      ),
      // Header actions (shown only when data is loaded)
      hasData && React.createElement("div", { style:{display:"flex",gap:8,alignItems:"center"} },
        exportMsg && React.createElement("span", { style:{fontSize:12,color:T.success} }, exportMsg),
        React.createElement(Btn, { onClick:handleExportCsv, variant:"ghost", small:true }, "⬇ Export CSV"),
        outputDir && React.createElement(Btn, { onClick:handleOpenFolder, variant:"ghost", small:true }, "📁 Open Output Folder"),
        React.createElement(Btn, { onClick:()=>{setDrawings([]);setClassified([]);setPdfPath("");setOutputDir("");setError("");setShowProgress(false);}, variant:"ghost", small:true },
          "↺ New File"
        )
      )
    ),

    // ── Scrollable body ──────────────────────────────────────────────────
    React.createElement("div", { style:{flex:1, overflowY:"auto", padding:"24px 28px"} },

      // ── Error ────────────────────────────────────────────────────────
      React.createElement(ErrorBanner, { message:error, onDismiss:()=>setError("") }),

      // ── No data: show setup panel ────────────────────────────────────
      !hasData && React.createElement("div", { style:{maxWidth:620,margin:"0 auto"} },

        React.createElement(DropZone, { onFile:setPdfPath, disabled:running }),

        pdfPath && React.createElement("div", {
          style:{ marginTop:12, padding:"10px 14px", background:T.card,
            border:`1px solid ${T.border}`, borderRadius:8,
            fontSize:12, color:T.muted2, fontFamily:"monospace",
            display:"flex", alignItems:"center", justifyContent:"space-between" }
        },
          React.createElement("span", { style:{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"} }, pdfPath),
          React.createElement("button", {
            onClick:()=>setPdfPath(""),
            style:{background:"none",border:"none",color:T.muted,cursor:"pointer",fontSize:14,flexShrink:0}
          }, "×")
        ),

        // Output dir picker
        React.createElement("div", { style:{marginTop:12,display:"flex",gap:8,alignItems:"center"} },
          React.createElement("div", {
            style:{ flex:1, padding:"9px 14px", background:T.card, border:`1px solid ${T.border}`,
              borderRadius:8, fontSize:12, color: outputDir?T.muted2:T.muted, fontFamily:"monospace",
              overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }
          }, outputDir || "Output folder (optional — defaults to PDF location)"),
          React.createElement(Btn, {
            onClick: async () => { const d=await window.electronAPI.pickOutputDir(); if(d) setOutputDir(d); },
            variant:"ghost", small:true, disabled:running
          }, "Browse")
        ),

        // ── Collapsible rule editor ──────────────────────────────────
        React.createElement("div", {
          style:{ marginTop:16, border:`1px solid ${T.border}`, borderRadius:10, overflow:"hidden" }
        },
          // Header toggle
          React.createElement("div", {
            onClick:()=>setRulesOpen(!rulesOpen),
            style:{ display:"flex", alignItems:"center", justifyContent:"space-between",
              padding:"12px 16px", background:T.card, cursor:"pointer",
              borderBottom: rulesOpen ? `1px solid ${T.border}` : "none" }
          },
            React.createElement("div", { style:{display:"flex",alignItems:"center",gap:10} },
              React.createElement("span", { style:{fontSize:14} }, "⚙️"),
              React.createElement("span", { style:{fontWeight:600,fontSize:13,color:T.text} }, "Work Package Rules"),
              React.createElement("span", {
                style:{ fontSize:11, color:T.muted, background:T.bg,
                  padding:"2px 8px", borderRadius:10, marginLeft:4 }
              }, `${rules.length} rules`),
              rulesSaved && React.createElement("span", {
                style:{ fontSize:11, color:T.success, marginLeft:4 }
              }, "✓ Saved")
            ),
            React.createElement("div", { style:{display:"flex",alignItems:"center",gap:10} },
              React.createElement("button", {
                onClick:(e)=>{ e.stopPropagation(); if (!running) setRules([...DEFAULT_RULES]); },
                style:{ fontSize:11, padding:"3px 10px", borderRadius:6,
                  background:"none", border:`1px solid ${T.border}`,
                  color: running ? T.muted : T.muted2,
                  cursor: running ? "not-allowed" : "pointer",
                  opacity: running ? 0.4 : 1 }
              }, "Reset to defaults"),
              React.createElement("span", {
                style:{ color:T.muted, fontSize:16,
                  transform: rulesOpen?"rotate(180deg)":"none", transition:"0.2s" }
              }, "▾")
            )
          ),

          // Expanded rule list
          rulesOpen && React.createElement("div", { style:{padding:"12px 16px", background:T.bg} },
            React.createElement("div", {
              style:{ fontSize:12, color:T.muted, marginBottom:12, lineHeight:1.5 }
            }, "Rules are evaluated top-to-bottom — first match wins. Changes are saved automatically and apply to all future runs."),

            rules.map((r,i) => React.createElement(RuleEditor, {
              key:r.id, rule:r, index:i,
              onChange:(idx,updated)=>{ const n=[...rules]; n[idx]=updated; setRules(n); },
              onDelete:(idx)=>setRules(rules.filter((_,j)=>j!==idx))
            })),

            // Add new rule button
            React.createElement("button", {
              onClick:()=>{ if (running) return;
                const newRule = {
                  id: "wp_custom_" + Date.now(),
                  name: "WP-New Custom Rule",
                  color: "#a855f7",
                  desc: "Describe this rule",
                  rules: {}
                };
                // Insert before wp99
                const idx = rules.findIndex(r=>r.id==="wp99");
                const next = [...rules];
                next.splice(idx > -1 ? idx : next.length, 0, newRule);
                setRules(next);
              },
              style:{ marginTop:8, width:"100%", padding:"8px", borderRadius:8,
                background:"none", border:`1px dashed ${T.border}`,
                color:T.muted, cursor: running ? "not-allowed" : "pointer",
                fontSize:12, opacity: running ? 0.4 : 1,
                transition:"0.15s" }
            }, "+ Add New Rule")
          )
        ),

        React.createElement(StatusBar, { lines:progressLines, visible:showProgress }),

        React.createElement("div", { style:{marginTop:20, display:"flex", alignItems:"center", gap:12} },
          React.createElement(Btn, { onClick:handleRun, disabled:running||!pdfPath },
            running ? "⏳ Processing…" : "▶  Run Sorter"
          ),
          running && React.createElement("button", {
            onClick: handleCancel,
            style:{ padding:"9px 18px", borderRadius:8, border:`1px solid ${T.danger}66`,
              background:"none", color:T.danger, fontWeight:600, fontSize:13,
              cursor:"pointer", transition:"0.15s" }
          }, "✕ Cancel")
        )
      ),

      // ── Has data: show results ───────────────────────────────────────
      hasData && React.createElement(React.Fragment, null,

        // Tabs
        React.createElement("div", {
          style:{ display:"flex",gap:2,marginBottom:24,background:T.card,
            borderRadius:10,padding:4,width:"fit-content" }
        },
          [["overview","Overview"],["rules","Rule Editor"],["table","Drawing List"]].map(([key,label]) =>
            React.createElement("button", {
              key, onClick:()=>setActiveTab(key),
              style:{ padding:"8px 20px",borderRadius:8,border:"none",
                background: activeTab===key ? T.accent : "transparent",
                color: activeTab===key ? "#fff" : T.muted2,
                fontWeight:600,fontSize:13,cursor:"pointer",transition:"0.15s" }
            }, label)
          )
        ),

        // ── Overview tab ─────────────────────────────────────────────
        activeTab==="overview" && React.createElement(React.Fragment, null,

          // Weight accuracy warning
          classified.some(d=>d.total_weight===0) && React.createElement("div", {
            style:{ display:"flex", alignItems:"center", gap:10,
              background:"#2d2200", border:`1px solid ${T.warning}44`,
              borderRadius:8, padding:"10px 14px", marginBottom:16, fontSize:12 }
          },
            React.createElement("span", { style:{fontSize:16,flexShrink:0} }, "⚠️"),
            React.createElement("span", { style:{color:T.muted2} },
              `${classified.filter(d=>d.total_weight===0).length} drawing(s) have no weight data — `,
              React.createElement("strong", { style:{color:T.warning} }, "total weight may be inaccurate."),
              " Check the Drawing List tab for details."
            )
          ),

          // Stat cards
          React.createElement("div", {
            style:{ display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:12,marginBottom:24 }
          },
            [
              { label:"Total Drawings", value:classified.length,                        color:T.accent },
              { label:"Work Packages",  value:wpList.filter(w=>!w.name.includes("99")).length, color:"#8b5cf6" },
              { label:"For Fabrication",value:classified.length-holdCount,               color:T.success },
              { label:"On Hold",        value:holdCount,                                 color:T.warning },
              { label:"Total Weight",
                value: totalWeight > 0
                  ? (totalWeight >= 1000 ? `${(totalWeight/1000).toFixed(1)}k lbs` : `${totalWeight} lbs`)
                  : "⚠ No data",
                color: totalWeight > 0 ? "#06b6d4" : T.warning },
            ].map(s => React.createElement("div", {
              key:s.label,
              style:{ background:T.card,border:`1px solid ${T.border}`,borderRadius:10,padding:"16px 18px" }
            },
              React.createElement("div", { style:{fontSize:10,color:T.muted,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:6} }, s.label),
              React.createElement("div", { style:{fontSize:24,fontWeight:700,color:s.color,fontFamily:"'JetBrains Mono',monospace"} }, s.value)
            ))
          ),

          // Charts
          React.createElement("div", {
            style:{ display:"grid",gridTemplateColumns:"260px 1fr",gap:16,marginBottom:24 }
          },
            React.createElement("div", { style:{background:T.card,border:`1px solid ${T.border}`,borderRadius:10,padding:20} },
              React.createElement("div", { style:{fontSize:12,fontWeight:600,color:T.muted,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:12} }, "Distribution"),
              React.createElement(DonutChart, { data:wpList })
            ),
            React.createElement("div", { style:{background:T.card,border:`1px solid ${T.border}`,borderRadius:10,padding:20} },
              React.createElement("div", { style:{fontSize:12,fontWeight:600,color:T.muted,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:12} }, "Drawings per Work Package"),
              React.createElement(HBar, { data:wpList.filter(w=>w.count>0) })
            )
          ),

          // WP cards
          React.createElement("div", { style:{fontSize:12,fontWeight:600,color:T.muted,textTransform:"uppercase",letterSpacing:"0.06em",marginBottom:12} }, "Work Package Breakdown"),
          React.createElement("div", {
            style:{ display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(300px,1fr))",gap:12 }
          },
            wpList.filter(w=>w.count>0).map(wp =>
              React.createElement("div", {
                key:wp.name,
                onClick:()=>{ setTableFilter(wp.name); setActiveTab("table"); },
                style:{ background:T.card,border:`1px solid ${T.border}`,
                  borderLeft:`4px solid ${wp.color}`,borderRadius:10,
                  padding:"16px 20px",cursor:"pointer" }
              },
                React.createElement("div", { style:{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:8} },
                  React.createElement("div", { style:{fontWeight:700,fontSize:14,color:T.text} }, wp.name),
                  React.createElement("div", {
                    style:{ background:wp.color+"22",color:wp.color,padding:"2px 10px",borderRadius:10,
                      fontSize:13,fontWeight:700,fontFamily:"'JetBrains Mono',monospace" }
                  }, wp.count)
                ),
                React.createElement("div", { style:{display:"flex",gap:16,fontSize:11,color:T.muted} },
                  React.createElement("span", null, `${wp.forFab} for fab`),
                  wp.hold>0 && React.createElement("span", { style:{color:T.warning} }, `${wp.hold} on hold`),
                  React.createElement("span", null, `${(wp.weight/1000).toFixed(1)}k lbs`)
                )
              )
            )
          )
        ),

        // ── Rule editor tab ──────────────────────────────────────────
        activeTab==="rules" && React.createElement("div", { style:{maxWidth:700} },
          React.createElement("div", { style:{fontSize:13,color:T.muted,marginBottom:16} },
            "Rules are evaluated top-to-bottom — first match wins. Changes re-classify drawings instantly."
          ),
          rules.map((r,i) => React.createElement(RuleEditor, {
            key:r.id, rule:r, index:i,
            onChange:(idx,updated)=>{ const n=[...rules]; n[idx]=updated; setRulesState(n); },
            onDelete:(idx)=>setRulesState(rules.filter((_,j)=>j!==idx)),
            locked:running
          }))
        ),

        // ── Drawing list tab ─────────────────────────────────────────
        activeTab==="table" && React.createElement(React.Fragment, null,
          React.createElement("div", { style:{display:"flex",gap:8,marginBottom:16,flexWrap:"wrap"} },
            React.createElement("button", {
              onClick:()=>setTableFilter("ALL"),
              style:{ padding:"6px 14px",borderRadius:6,
                border:`1px solid ${T.border}`,
                background: tableFilter==="ALL"?T.accent:T.card,
                color: tableFilter==="ALL"?"#fff":T.muted2,
                fontSize:12,fontWeight:600,cursor:"pointer" }
            }, `All (${classified.length})`),
            wpList.filter(w=>w.count>0).map(wp =>
              React.createElement("button", {
                key:wp.name, onClick:()=>setTableFilter(wp.name),
                style:{ padding:"6px 14px",borderRadius:6,
                  border:`1px solid ${tableFilter===wp.name?wp.color:T.border}`,
                  background: tableFilter===wp.name?wp.color+"22":T.card,
                  color: tableFilter===wp.name?wp.color:T.muted2,
                  fontSize:12,fontWeight:600,cursor:"pointer",
                  display:"flex",alignItems:"center",gap:6 }
              },
                React.createElement("span", { style:{width:8,height:8,borderRadius:2,background:wp.color} }),
                `${wp.name.replace(/^WP-\d+\s*/,"")} (${wp.count})`
              )
            )
          ),
          React.createElement(DrawingTable, { drawings:classified, filter:tableFilter })
        )
      )
    ),

    // ── Footer ───────────────────────────────────────────────────────────
    React.createElement("div", {
      style:{ padding:"12px 28px", borderTop:`1px solid ${T.border}`,
        fontSize:11,color:T.muted, display:"flex",justifyContent:"space-between",flexShrink:0 }
    },
      React.createElement("span", null,
        pdfPath
          ? React.createElement("span", { style:{fontFamily:"monospace"} }, pdfPath)
          : "No file loaded"
      ),
      React.createElement("span", null, "Work Package Sorter v1.1")
    )
  );
}

// ── Mount ─────────────────────────────────────────────────────────────────
const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(React.createElement(App));
