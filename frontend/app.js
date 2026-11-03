(()=>{"use strict";
const API=String((window.ADER_CONFIG||{}).API_BASE||"").replace(/\/$/,"");
const s={user:null,guilds:[],guild:null,overview:{},health:{},resources:{roles:[],channels:[]},page:"overview"};
const appEditor={panelNumber:1,draft:null,panels:[],applications:[]};
const $=x=>document.querySelector(x),esc=x=>String(x??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function api(p,o={}){
  const method=String(o.method||"GET").toUpperCase();
  const maxAttempts=method==="GET"?3:1;
  for(let attempt=0;attempt<maxAttempts;attempt++){
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),18000);
    try{
      const r=await fetch(API+p,{
        credentials:"include",
        cache:"no-store",
        headers:{"Content-Type":"application/json",...(o.headers||{})},
        ...o,
        signal:controller.signal
      });
      const t=await r.text();
      let d={};
      try{d=t?JSON.parse(t):{}}catch{d={detail:t}}
      if(r.status===503&&method==="GET"&&attempt<maxAttempts-1){
        await new Promise(resolve=>setTimeout(resolve,1200));
        continue;
      }
      if(r.status===401)throw Error("AUTH");
      if(!r.ok){const err=Error(d.detail||d.message||"HTTP "+r.status);err.status=r.status;err.path=p;throw err;}
      return d;
    }catch(e){
      if(e.name==="AbortError")throw Error("انتهت مهلة الاتصال بخادم Ader بعد 18 ثانية.");
      if(e instanceof TypeError)throw Error("تعذر الاتصال بالـAPI. تحقق من تشغيل Backend وعنوانه.");
      throw e;
    }finally{
      clearTimeout(timeout);
    }
  }
  throw Error("تعذر استعادة اتصال ANO BOT بعد عدة محاولات. تحقق من اتصال Discord.");
}
function icon(g){return g&&g.icon?'<img src="https://cdn.discordapp.com/icons/'+g.id+'/'+g.icon+'.png?size=128" alt="">':esc((g?.name||"A")[0])}
function showLogin(){ $("#loading").classList.add("hidden");$("#app").classList.add("hidden");$("#login").classList.remove("hidden")}
function showApp(){ $("#loading").classList.add("hidden");$("#login").classList.add("hidden");$("#app").classList.remove("hidden")}
function warn(m){$("#apiWarning").textContent=m;$("#apiWarning").classList.remove("hidden")}
function loginError(m){
  const host=$("#login .login-panel-inner");
  if(!host)return;
  let box=$("#loginApiError");
  if(!box){
    box=document.createElement("div");
    box.id="loginApiError";
    box.className="login-note";
    host.appendChild(box);
  }
  box.textContent=m;
  box.classList.remove("hidden");
}
function clearWarn(){$("#apiWarning").classList.add("hidden")}
function head(t,d){return '<div class="page-head"><div class="page-head-copy"><div class="page-kicker">ANO BOT CONTROL CENTER</div><h1>'+t+'</h1><p>'+d+'</p></div>'+(s.guild?'<div class="page-meta"><span class="status-dot"></span><span>'+esc(s.guild.name)+'</span></div>':'')+'</div>'}
function card(t,b){return '<section class="card"><div class="card-head"><b>'+t+'</b></div><div class="card-body">'+b+'</div></section>'}
function stat(a,b,c){return '<div class="stat"><span>'+a+'</span><i>'+c+'</i><strong>'+b+'</strong></div>'}
function selectRoles(id,value){return '<select id="'+id+'"><option value="">اختار رتبة…</option>'+s.resources.roles.map(r=>'<option value="'+r.id+'" '+(String(value||"")===String(r.id)?"selected":"")+'>'+esc(r.name)+'</option>').join("")+'</select>'}
function selectChannels(id,value){return '<select id="'+id+'"><option value="">اختار قناة…</option>'+s.resources.channels.filter(c=>["text","news"].includes(c.type)).map(c=>'<option value="'+c.id+'" '+(String(value||"")===String(c.id)?"selected":"")+'># '+esc(c.name)+'</option>').join("")+'</select>'}
async function loadGuild(){
  if(!s.guild?.id)return;
  const guildId=String(s.guild.id);
  const healthTask=api("/healthz").then(data=>{
    s.health=data||{};
  }).catch(error=>{
    s.health={backend_ok:false,bot_ready:false,database:false,error:error.message};
  });
  try{
    [s.overview,s.resources]=await Promise.all([
      api("/api/guilds/"+guildId+"/overview"),
      api("/api/guilds/"+guildId+"/resources")
    ]);
    clearWarn();
    localStorage.setItem("ader_selected_guild_id",guildId);
  }catch(e){
    const reason=e.status===403
      ? "ما قدرناش نتحقق من صلاحية Manage Server حالياً. حدّث الصفحة، وإذا استمر المشكل سجّل الخروج ثم الدخول من Discord."
      : e.status===404
        ? "البوت غير متصل بهذا السيرفر حالياً. تأكد من إضافته للسيرفر ثم حدّث الصفحة."
        : e.message;
    warn("تعذر تحميل بيانات السيرفر: "+reason);
  }
  await healthTask;
  $("#guildName").textContent=s.guild.name;
  $("#guildIcon").innerHTML=icon(s.guild);
}
async function pick(id){
  const selected=s.guilds.find(g=>String(g.id)===String(id));
  if(!selected)return;
  s.guild=selected;
  s.overview={};
  s.resources={roles:[],channels:[]};
  $("#guildSelect").value=String(selected.id);
  localStorage.setItem("ader_selected_guild_id",String(selected.id));
  await loadGuild();
  await render();
}
const titles={overview:"Overview",servers:"السيرفرات",analytics:"Analytics",moderation:"Moderation",automod:"AutoMod",economy:"ANORIS Economy",tickets:"التذاكر",levels:"Levels",giveaways:"Giveaways",welcome:"Welcome",commands:"Commands",shortcuts:"Shortcuts",resources:"القنوات والرتب",teams:"Teams",applications:"تقديم الإدارة",logs:"السجلات",security:"Security",premium:"Ader Premium",settings:"Settings"};
const desc={overview:"نظرة شاملة على حالة ANO BOT والسيرفر.",analytics:"إحصائيات النشاط المسجلة فعلياً.",moderation:"الإشراف والتحذيرات والحماية التلقائية.",automod:"إعدادات AutoMod المبنية على نظام ANO BOT.",economy:"إعدادات ANORIS وترتيب الأرصدة.",tickets:"لوحات التذاكر والتذاكر المفتوحة.",levels:"ترتيب XP والمستويات.",giveaways:"بيانات الهدايا المحفوظة.",welcome:"الترحيب والتحقق.",commands:"تحكم مباشر في أوامر ANO BOT.",shortcuts:"إدارة الاختصارات النصية.",resources:"القنوات والرتب المتاحة.",teams:"الفرق الموثقة وإعداداتها.",applications:"أنشئ لوحات تقديم الإدارة، عدّل الأسئلة، وانظر الطلبات وراجعها من مكان واحد.",logs:"سجل أحداث الأعضاء والرسائل والقنوات والرتب والتذاكر والتقديمات.",security:"حالة جلسة الدخول وصلاحياتك.",premium:"إدارة خطة Premium والـLinked Bot بنفس بيانات ANO BOT.",settings:"تفعيل وتعطيل أنظمة ANO BOT."};
function showSectionError(error){
  const message=String(error?.message||"تعذر الاتصال بخادم Ader.");
  const premium=error?.status===402;
  $("#root").innerHTML='<div class="empty"><b>تعذر تحميل '+esc(titles[s.page]||"القسم")+'</b><p>'+esc(message)+'</p>'+
    (premium?'<button class="action" data-go="premium">عرض Ader Premium</button>':'')+
    '<button class="action" id="retry-page">إعادة المحاولة</button></div>';
  warn("تعذر تحميل القسم: "+message);
  $("#retry-page")?.addEventListener("click",()=>render());
}
async function render(){
  const p=s.page;
  $("#title").textContent=titles[p]||p;
  document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("active",b.dataset.page===p));
  try{
    if(p==="overview")overview();
    else if(p==="servers")servers();
    else if(p==="resources")resources();
    else if(p==="analytics")await analytics();
    else if(p==="moderation"||p==="automod")await moderation();
    else if(p==="economy")await economy();
    else if(p==="levels")await levels();
    else if(p==="tickets")await tickets();
    else if(p==="welcome")await welcome();
    else if(p==="commands")await commands();
    else if(p==="shortcuts")await shortcuts();
    else if(p==="teams")await teams();
    else if(p==="applications")await applications();
    else if(p==="logs")await logs();
    else if(p==="security")security();
    else if(p==="premium")await premium();
    else if(p==="settings")await settings();
    else showSectionError(new Error("هذا القسم غير متاح حالياً."));
    clearWarn();
  }catch(e){
    if(e.message==="AUTH"){
      showLogin();
      return;
    }
    if(e.status===404&&e.message==="البوت غير متصل بهذا الخادم حالياً"){
      try{
        const fresh=await api("/api/guilds");
        s.guilds=(fresh.guilds||[]).filter(x=>x&&x.id);
        const stillAvailable=s.guilds.find(g=>String(g.id)===String(s.guild?.id));
        if(!stillAvailable&&s.guilds.length){
          s.guild=s.guilds[0];
          $("#guildSelect").innerHTML=s.guilds.map(g=>'<option value="'+g.id+'">'+esc(g.name)+'</option>').join("");
          $("#guildSelect").value=String(s.guild.id);
          localStorage.setItem("ader_selected_guild_id",String(s.guild.id));
          await loadGuild();
          await render();
          return;
        }
        if(!s.guilds.length){
          $("#root").innerHTML='<div class="empty">ما كاين حتى سيرفر ظاهر فيه Ader حالياً. تأكد أن البوت متصل بالسيرفر، ثم أعد تشغيل البوت وحدّث الصفحة.</div>';
          warn("تعذر العثور على سيرفر متاح للبوت.");
          return;
        }
        showSectionError(new Error("السيرفر ظاهر في القائمة، لكن الـBackend لا يراه داخل البوت. أعد تشغيل ANO BOT ثم حدّث الصفحة."));
      }catch(refreshError){
        showSectionError(refreshError);
      }
    }else{
      showSectionError(e);
    }
  }
  bindCommon();
}
function overview(){
  const o=s.overview||{},u=s.user||{},g=s.guild||{},h=s.health||{};
  const display=esc(u.global_name||u.username||"Discord User");
  const backendOk=h.backend_ok===true;
  const botReady=backendOk&&h.bot_ready===true;
  const databaseReady=backendOk&&h.database===true;
  const botLabel=botReady?"Online":backendOk?"Starting":"Offline";
  const apiLabel=backendOk?"Connected":"Unavailable";
  const dbLabel=databaseReady?"Connected":backendOk?"Disconnected":"Unknown";
  $("#root").innerHTML=
    '<section class="hero-card dashboard-hero"><div><div class="page-kicker">LIVE SERVER OVERVIEW</div><h2>مرحباً، '+display+'</h2><p>إدارة ANO BOT ومراقبة أنظمة السيرفر من لوحة واحدة، مع بيانات مباشرة من الخادم.</p></div><div class="dashboard-hero-status"><span class="status-dot '+(botReady?"":"status-off")+'"></span><div><b>'+esc(g.name||"Your server")+'</b><small>ANO BOT · '+botLabel+'</small></div></div></section>'+
    '<div class="stats">'+stat("الأعضاء",Number(o.members||0).toLocaleString(),"♙")+stat("القنوات",Number(o.channels||0).toLocaleString(),"#")+stat("الرتب",Number(o.roles||0).toLocaleString(),"◆")+stat("التذاكر المفتوحة",Number(o.open_tickets||0).toLocaleString(),"🎫")+'</div>'+
    '<div class="cards overview-cards">'+
      card("حالة النظام المباشرة",'<div class="health"><div><span>ANO BOT Runtime</span><b class="'+(botReady?"on":"off")+'">● '+botLabel+'</b></div><div><span>Backend API</span><b class="'+(backendOk?"on":"off")+'">● '+apiLabel+'</b></div><div><span>Database</span><b class="'+(databaseReady?"on":"off")+'">● '+dbLabel+'</b></div><div><span>Commands</span><b>'+Number(o.commands||0).toLocaleString()+' registered</b></div><div><span>Guild Access</span><b>'+s.guilds.length+' manageable</b></div><div><span>Worker</span><b class="'+(h.worker==="ok"?"on":"off")+'">● '+(h.worker==="ok"?"Online":"Unknown")+'</b></div></div>')+
      card("إجراءات سريعة",'<p class="card-note">انتقل مباشرة إلى إعدادات النظام المطلوبة.</p><div class="quick"><button class="action" data-go="moderation">🛡 Moderation</button><button class="action" data-go="automod">🤖 AutoMod</button><button class="action" data-go="economy">🪙 ANORIS</button><button class="action" data-go="tickets">🎫 Tickets</button><button class="action" data-go="settings">⚙ Settings</button></div>')+
    '</div>'+
    '<div class="cards overview-cards secondary-overview">'+
      card("ملخص السيرفر",'<div class="snapshot"><div><span>Members</span><b>'+Number(o.members||0).toLocaleString()+'</b></div><div><span>Channels</span><b>'+Number(o.channels||0).toLocaleString()+'</b></div><div><span>Roles</span><b>'+Number(o.roles||0).toLocaleString()+'</b></div><div><span>Open Tickets</span><b>'+Number(o.open_tickets||0).toLocaleString()+'</b></div></div>')+
      card("حالة ANO BOT",'<div class="status-summary"><div><span class="status-dot '+(botReady?"":"status-off")+'"></span><b>'+ (botReady?"البوت متصل وجاهز":backendOk?"الخادم الخلفي متصل والبوت غير جاهز":"تعذر الوصول إلى الخادم الخلفي")+'</b></div><p>حالة الاتصال و قاعدة البيانات هنا كتجي من فحص Backend الحقيقي، ماشي حالة ثابتة فالتصميم.</p></div>')+
    '</div>';
}
function servers(){$("#root").innerHTML=head("السيرفرات","اختار السيرفر للإدارة.")+'<div class="server-grid">'+s.guilds.map(g=>'<button class="server" data-guild="'+g.id+'"><div class="avatar">'+icon(g)+'</div><b>'+esc(g.name)+'</b><small>'+(g.administrator?"Administrator":"Manage Server")+'</small></button>').join("")+'</div>'}
function resources(){const r=s.resources||{};$("#root").innerHTML=head("القنوات والرتب",desc.resources)+'<div class="cards">'+card("الرتب",'<div class="list">'+(r.roles||[]).map(x=>'<div><b>'+esc(x.name)+'</b><small>Position '+x.position+'</small></div>').join("")+'</div>')+card("القنوات",'<div class="list">'+(r.channels||[]).map(x=>'<div><b># '+esc(x.name)+'</b><small>'+esc(x.type)+'</small></div>').join("")+'</div>')+'</div>'}
async function analytics(){const d=await api("/api/guilds/"+s.guild.id+"/analytics?days=7"),c=d.counts||{};$("#root").innerHTML=head("Analytics",desc.analytics)+'<div class="stats">'+stat("الأحداث",d.total||0,"◔")+stat("Messages",c.message||0,"💬")+stat("Joins",c.member_join||0,"↗")+stat("Leaves",c.member_leave||0,"↘")+'</div>'+card("Daily Activity",'<div class="list">'+Object.entries(d.daily||{}).sort().map(([day,v])=>'<div><b>'+esc(day)+'</b><small>messages '+(v.message||0)+' · joins '+(v.member_join||0)+' · leaves '+(v.member_leave||0)+'</small></div>').join("")+'</div>')}
async function moderation(){const d=await api("/api/guilds/"+s.guild.id+"/moderation"),m=d.config||{},a=m.auto_mod||{};$("#root").innerHTML=head(titles[s.page],desc[s.page])+'<div class="cards">'+card("Module",'<label class="toggleline">Enabled <input type="checkbox" id="mod-enabled" '+(m.enabled!==false?"checked":"")+'></label>')+card("AutoMod",'<label>Spam Detection<input type="checkbox" id="spam" '+(a.spam_detection!==false?"checked":"")+'></label><label>Max Mentions<input id="mentions" type="number" min="0" max="50" value="'+(a.max_mentions??5)+'"></label><label>Link Filter<input type="checkbox" id="links" '+(a.link_filter===true?"checked":"")+'></label><button class="primary smallbtn" id="save-mod">حفظ</button>')+card("Warnings",'<div class="big-number">'+(d.warning_count||0)+'</div>'+(d.warnings||[]).slice(0,10).map(w=>'<div class="logline"><b>User '+w.user_id+'</b><small>'+esc(w.reason)+'</small></div>').join(""))+'</div>';$("#save-mod").onclick=async()=>{
  try{
    await api("/api/guilds/"+s.guild.id+"/modules/moderation",{method:"PUT",body:JSON.stringify({enabled:$("#mod-enabled").checked,auto_mod:{spam_detection:$("#spam").checked,max_mentions:Number($("#mentions").value),link_filter:$("#links").checked}})});
    clearWarn();
    await loadGuild();
    await moderation();
  }catch(e){warn("تعذر حفظ إعدادات الإشراف: "+e.message)}
  }}
async function economy(){const d=await api("/api/guilds/"+s.guild.id+"/economy"),c=d.config||{};$("#root").innerHTML=head("ANORIS Economy",desc.economy)+'<div class="cards">'+card("Currency",'<label>الاسم<input id="cur-name" value="'+esc(c.currency_name||"ANORIS")+'"></label><label>الرمز<input id="cur-symbol" value="'+esc(c.currency_symbol||"🪙")+'"></label><button class="primary smallbtn" id="save-econ">حفظ</button>')+card("Leaderboard",'<div class="list">'+(d.leaderboard||[]).map((x,i)=>'<div><b>#'+(i+1)+' · User '+x.user_id+'</b><span>'+Number(x.balance||0).toLocaleString()+' '+esc(c.currency_name||"ANORIS")+'</span></div>').join("")+'</div>')+'</div>';$("#save-econ").onclick=async()=>{
  try{
    await api("/api/guilds/"+s.guild.id+"/modules/economy",{method:"PUT",body:JSON.stringify({currency_name:$("#cur-name").value,currency_symbol:$("#cur-symbol").value})});
    clearWarn();
    await economy();
  }catch(e){warn("تعذر حفظ إعدادات الاقتصاد: "+e.message)}
  }}
async function levels(){const d=await api("/api/guilds/"+s.guild.id+"/levels");$("#root").innerHTML=head("Levels",desc.levels)+card("XP Leaderboard",'<div class="list">'+(d.users||[]).map((x,i)=>'<div><b>#'+(i+1)+' · User '+x.user_id+'</b><small>Level '+x.level+' · '+Number(x.xp||0).toLocaleString()+' XP</small></div>').join("")+'</div>')}
let ticketEditor={panelId:null,panels:[],settings:{},options:[]};

function resourceOptions(items, value, empty){
  return '<option value="">'+esc(empty)+'</option>'+(items||[]).map(x=>'<option value="'+x.id+'" '+(String(value||'')===String(x.id)?'selected':'')+'>'+esc(x.name)+'</option>').join('');
}
function ticketTypeCard(x,i){
  x=x||{};
  return '<div class="ticket-type-card" data-ticket-type="'+i+'">'+
    '<div class="ticket-type-head"><b>النوع '+(i+1)+'</b><button class="action danger" data-remove-type="'+i+'">حذف النوع</button></div>'+
    '<div class="form-grid">'+
      '<label>اسم النوع<input data-tf="name" value="'+esc(x.name||'الدعم العام')+'"></label>'+
      '<label>الرمز التعبيري<input data-tf="emoji" value="'+esc(x.emoji||'🎫')+'"></label>'+
      '<label>نمط زر النوع<select data-tf="button_style"><option value="primary" '+(x.button_style==='primary'?'selected':'')+'>أساسي</option><option value="secondary" '+(x.button_style==='secondary'?'selected':'')+'>ثانوي</option><option value="success" '+(x.button_style==='success'?'selected':'')+'>نجاح</option><option value="danger" '+(x.button_style==='danger'?'selected':'')+'>تحذير</option></select></label>'+
      '<label>الأولوية<select data-tf="priority"><option value="low" '+(x.priority==='low'?'selected':'')+'>منخفضة</option><option value="normal" '+(!x.priority||x.priority==='normal'?'selected':'')+'>عادية</option><option value="high" '+(x.priority==='high'?'selected':'')+'>مرتفعة</option><option value="urgent" '+(x.priority==='urgent'?'selected':'')+'>عاجلة</option></select></label>'+
    '</div>'+
    '<div class="form-grid">'+
      '<label>وصف النوع<input data-tf="description" value="'+esc(x.description||'افتح تذكرة للحصول على المساعدة.')+'"></label>'+
      '<label>اسم القناة<input data-tf="ticket_name" value="'+esc(x.ticket_name||'ticket-{number}-{user}')+'"></label>'+
      '<label>فئة التذاكر<select data-tf="category_id">'+resourceOptions(s.resources.channels.filter(a=>a.type==='category'),x.category_id,'استخدم الفئة العامة')+'</select></label>'+
      '<label>رتبة الدعم<select data-tf="support_role_id">'+resourceOptions(s.resources.roles,x.support_role_id,'استخدم رتبة الدعم العامة')+'</select></label>'+
    '</div>'+
    '<div class="form-grid">'+
      '<label>اللون<input data-tf="color" type="color" value="'+esc(x.color||'#5865F2')+'"></label>'+
      '<label>الصورة داخل التذكرة<input data-tf="image_url" value="'+esc(x.image_url||'')+'"></label>'+
      '<label>النص السفلي<input data-tf="footer" value="'+esc(x.footer||'دعم Ader')+'"></label>'+
      '<label>الحد الأقصى المفتوح<input data-tf="max_open" type="number" min="1" max="10" value="'+(Number(x.max_open)||1)+'"></label>'+
    '</div>'+
    '<label class="inline-check"><input data-tf="enabled" type="checkbox" '+(x.enabled!==false?'checked':'')+'><span>تفعيل هذا النوع</span></label>'+
  '</div>';
}
function readTicketTypes(){
  const rows=[...document.querySelectorAll('[data-ticket-type]')];
  return rows.map(row=>{
    const get=k=>row.querySelector('[data-tf="'+k+'"]');
    return {
      name:get('name')?.value||'الدعم العام',emoji:get('emoji')?.value||'🎫',
      button_style:get('button_style')?.value||'primary',priority:get('priority')?.value||'normal',
      description:get('description')?.value||'افتح تذكرة للحصول على المساعدة.',
      ticket_name:get('ticket_name')?.value||'ticket-{number}-{user}',
      category_id:get('category_id')?.value||null,support_role_id:get('support_role_id')?.value||null,
      color:get('color')?.value||'#5865F2',image_url:get('image_url')?.value||null,
      footer:get('footer')?.value||'دعم Ader',max_open:Number(get('max_open')?.value||1),
      enabled:Boolean(get('enabled')?.checked)
    };
  });
}
function ticketForm(panel){
  const p=panel||{};
  const ts=p.settings||{};
  return '<div class="ticket-builder">'+
    '<div class="ticket-builder-top"><div><h2>'+((p.id?'تعديل لوحة التذاكر #'+p.id:'إنشاء لوحة تذاكر جديدة'))+'</h2><p>اضبط كل شيء من مكان واحد، ثم احفظ أو احفظ وانشر مباشرة.</p></div><div class="ticket-actions"><button class="action" id="ticket-new">لوحة جديدة</button><button class="action" id="ticket-save">حفظ</button><button class="primary smallbtn" id="ticket-publish">حفظ ونشر</button>'+ (p.id ? '<button class="action danger" id="ticket-delete">حذف اللوحة</button>' : '') +'</div></div>'+
    '<div class="cards">'+
      card('مظهر اللوحة',
        '<div class="form-grid">'+
          '<label>العنوان<input id="tp-title" value="'+esc(p.title||'الدعم الفني')+'"></label>'+
          '<label>اللون<input id="tp-color" type="color" value="'+esc(ts.color||'#5865F2')+'"></label>'+
          '<label>قناة النشر<select id="tp-channel">'+resourceOptions(s.resources.channels.filter(a=>a.type==='text'||a.type==='news'),p.channel_id,'اختر القناة')+'</select></label>'+
          '<label>فئة التذاكر الافتراضية<select id="tp-category">'+resourceOptions(s.resources.channels.filter(a=>a.type==='category'),p.category_id,'اختر الفئة')+'</select></label>'+
        '</div>'+
        '<label>الوصف<textarea id="tp-description" rows="4">'+esc(p.description||'اختر نوع الطلب لفتح تذكرة.')+'</textarea></label>'+
        '<div class="form-grid">'+
          '<label>الصورة<input id="tp-image" value="'+esc(p.image_url||'')+'"></label>'+
          '<label>الصورة المصغرة<input id="tp-thumb" value="'+esc(ts.thumbnail_url||'')+'"></label>'+
          '<label>التذييل<input id="tp-footer" value="'+esc(ts.footer||'دعم Ader')+'"></label>'+
          '<label>نمط العرض<select id="tp-mode"><option value="buttons" '+(p.mode!=='select'?'selected':'')+'>أزرار</option><option value="select" '+(p.mode==='select'?'selected':'')+'>قائمة اختيار</option></select></label>'+
        '</div>'+
        '<label>وصف التذكرة الافتراضي<textarea id="tp-ticket-desc" rows="3">'+esc(p.ticket_description||'يرجى شرح المشكلة بالتفصيل.')+'</textarea></label>'+
      '')+
      card('إعدادات التشغيل',
        '<div class="form-grid">'+
          '<label>رتبة الدعم العامة<select id="ts-role">'+resourceOptions(s.resources.roles,ticketEditor.settings.default_support_role_id,'بدون رتبة محددة')+'</select></label>'+
          '<label>فئة التذاكر العامة<select id="ts-category">'+resourceOptions(s.resources.channels.filter(a=>a.type==='category'),ticketEditor.settings.default_category_id,'بدون فئة محددة')+'</select></label>'+
          '<label>قناة السجلات<select id="ts-log">'+resourceOptions(s.resources.channels.filter(a=>a.type==='text'||a.type==='news'),ticketEditor.settings.log_channel_id,'بدون سجل')+'</select></label>'+
          '<label>قناة السجلات النصية<select id="ts-transcript">'+resourceOptions(s.resources.channels.filter(a=>a.type==='text'||a.type==='news'),ticketEditor.settings.transcript_channel_id,'بدون سجل نصي')+'</select></label>'+
        '</div>'+
        '<div class="toggle-grid">'+
          '<label class="toggle-row"><input id="ts-enabled" type="checkbox" '+(ticketEditor.settings.enabled!==false?'checked':'')+'><span>تفعيل النظام</span><b>✓</b></label>'+
          '<label class="toggle-row"><input id="ts-claim" type="checkbox" '+(ticketEditor.settings.claim_enabled!==false?'checked':'')+'><span>تفعيل تولّي التذاكر</span><b>🙋</b></label>'+
          '<label class="toggle-row"><input id="ts-rating" type="checkbox" '+(ticketEditor.settings.rating_enabled!==false?'checked':'')+'><span>تفعيل التقييم بعد الإغلاق</span><b>★</b></label>'+
          '<label class="toggle-row"><input id="ts-userclose" type="checkbox" '+(ticketEditor.settings.allow_user_close!==false?'checked':'')+'><span>السماح لصاحب التذكرة بالإغلاق</span><b>🔒</b></label>'+
          '<label class="toggle-row"><input id="ts-userreopen" type="checkbox" '+(ticketEditor.settings.allow_user_reopen===true?'checked':'')+'><span>السماح لصاحب التذكرة بإعادة الفتح</span><b>↺</b></label>'+
          '<label class="toggle-row"><input id="ts-add" type="checkbox" '+(ticketEditor.settings.allow_member_add!==false?'checked':'')+'><span>إضافة أعضاء</span><b>＋</b></label>'+
          '<label class="toggle-row"><input id="ts-remove" type="checkbox" '+(ticketEditor.settings.allow_member_remove!==false?'checked':'')+'><span>إزالة أعضاء</span><b>−</b></label>'+
          '<label class="toggle-row"><input id="ts-rename" type="checkbox" '+(ticketEditor.settings.allow_rename!==false?'checked':'')+'><span>إعادة التسمية</span><b>✎</b></label>'+
          '<label class="toggle-row"><input id="ts-lock" type="checkbox" '+(ticketEditor.settings.allow_lock!==false?'checked':'')+'><span>قفل وفتح التذكرة</span><b>🔐</b></label>'+
          '<label class="toggle-row"><input id="ts-keep" type="checkbox" '+(ticketEditor.settings.keep_closed!==false?'checked':'')+'><span>الإبقاء على التذكرة بعد الإغلاق</span><b>▣</b></label>'+
        '</div>'+
        '<div class="form-grid">'+
          '<label>الحد الأقصى للتذاكر المفتوحة للعضو<input id="ts-max" type="number" min="1" max="10" value="'+(Number(ticketEditor.settings.max_open_per_user)||1)+'"></label>'+
          '<label>قالب اسم القناة<input id="ts-template" value="'+esc(ticketEditor.settings.channel_name_template||'ticket-{number}-{user}')+'"></label>'+
          '<label>الحذف التلقائي بعد الإغلاق بالثواني<input id="ts-delete" type="number" min="0" max="3600" value="'+(Number(ticketEditor.settings.delete_after_close_seconds)||0)+'"></label>'+
        '</div>'
      )+
    '</div>'+
    '<section class="card ticket-types-card"><div class="card-head"><div class="ticket-types-title"><b>أنواع التذاكر</b><button class="action" id="ticket-add-type">إضافة نوع</button></div></div><div class="card-body" id="ticket-types">'+ticketEditor.options.map(ticketTypeCard).join('')+'</div></section>'+
    '<div class="ticket-hint">المتغيرات المتاحة لاسم القناة: {number} رقم التذكرة، {user} اسم العضو، {id} معرف العضو، {type} نوع التذكرة.</div>'+
  '</div>';
}
async function tickets(){
  const d=await api("/api/guilds/"+s.guild.id+"/tickets");
  ticketEditor.panels=d.panels||[];ticketEditor.settings=d.settings||{};
  const selected=ticketEditor.panels.find(p=>Number(p.id)===Number(ticketEditor.panelId))||ticketEditor.panels[0]||null;
  ticketEditor.panelId=selected?Number(selected.id):null;
  ticketEditor.options=(selected?.options||[]).map(x=>({...x}));
  const counts=(d.tickets||[]).reduce((a,x)=>(a[x.status]=(a[x.status]||0)+1,a),{});
  const statusName={open:"مفتوحة",locked:"مقفلة",closed:"مغلقة",deleted:"محذوفة"};
  $("#root").innerHTML=head("نظام التذاكر", "نظام دعم احترافي قابل للتخصيص بالكامل، مع لوحات متعددة وسجلات وتقييمات وصلاحيات دقيقة.")+
    '<div class="stats ticket-stats">'+stat("المفتوحة",counts.open||0,"●")+stat("المقفلة",counts.locked||0,"◐")+stat("المغلقة",counts.closed||0,"✓")+stat("اللوحات",ticketEditor.panels.length,"▣")+'</div>'+
    '<div class="ticket-layout">'+
      '<aside class="ticket-panel-list"><div class="ticket-panel-list-head"><b>لوحات التذاكر</b><button class="action" id="ticket-create-top">＋</button></div>'+
      '<div class="list">'+(ticketEditor.panels.length?ticketEditor.panels.map(p=>'<button class="ticket-panel-item '+(ticketEditor.panelId===p.id?'active':'')+'" data-ticket-panel="'+p.id+'"><b>#'+p.id+' · '+esc(p.title)+'</b><small>'+(p.mode==='select'?'قائمة اختيار':'أزرار')+' · '+(p.options||[]).length+' أنواع</small></button>').join(''):'<div class="empty small">لا توجد لوحة بعد. أنشئ أول لوحة من الزر أعلاه.</div>')+'</div></aside>'+
      '<section id="ticket-editor-host">'+ticketForm(ticketEditor.panels.find(p=>p.id===ticketEditor.panelId)||ticketEditor.panels[0])+'</section>'+
    '</div>'+
    card("آخر التذاكر",'<div class="list">'+(d.tickets||[]).slice(0,25).map(x=>{
      const dt=x.data||{}; const r=x.rating; return '<div><span><b>#'+x.id+' · '+esc(dt.type||'دعم')+'</b><small>العضو <@'+esc(x.user_id)+'></small></span><span><b>'+(statusName[x.status]||esc(x.status))+'</b><small>'+(r?('التقييم '+r.rating+'/5'):'بدون تقييم')+'</small></span></div>';
    }).join('')+'</div>');

  bindTicketEditor();
}
function bindTicketEditor(){
  document.querySelectorAll("[data-ticket-panel]").forEach(b=>b.onclick=()=>{
    ticketEditor.panelId=Number(b.dataset.ticketPanel);
    const p=ticketEditor.panels.find(x=>x.id===ticketEditor.panelId)||{};
    ticketEditor.options=(p.options||[]).map(x=>({...x}));
    $("#ticket-editor-host").innerHTML=ticketForm(p);bindTicketEditor();
  });
  const add=()=>{ticketEditor.options.push({name:"قسم جديد",emoji:"🎫",description:"افتح تذكرة للحصول على المساعدة.",ticket_name:"ticket-{number}-{user}",button_style:"primary",priority:"normal",max_open:1,enabled:true,color:"#5865F2",footer:"دعم Ader"});const idx=ticketEditor.options.length-1;$("#ticket-types").insertAdjacentHTML("beforeend",ticketTypeCard(ticketEditor.options[idx],idx));const rb=document.querySelector('[data-remove-type="'+idx+'"]');if(rb)rb.onclick=()=>{if(document.querySelectorAll("[data-ticket-type]").length<=1)return warn("يجب الإبقاء على نوع واحد على الأقل.");rb.closest("[data-ticket-type]")?.remove();};};
  document.getElementById("ticket-add-type")?.addEventListener("click",add);
  document.getElementById("ticket-create-top")?.addEventListener("click",()=>{ticketEditor.panelId=null;ticketEditor.options=[{name:"الدعم العام",emoji:"🎫",description:"فتح تذكرة دعم",ticket_name:"ticket-{number}-{user}",button_style:"primary",priority:"normal",max_open:1,enabled:true,color:"#5865F2",footer:"دعم Ader"}];document.getElementById("ticket-editor-host").innerHTML=ticketForm(null);bindTicketEditor();});
  document.getElementById("ticket-new")?.addEventListener("click",()=>{ticketEditor.panelId=null;ticketEditor.options=[{name:"الدعم العام",emoji:"🎫",description:"فتح تذكرة دعم",ticket_name:"ticket-{number}-{user}",button_style:"primary",priority:"normal",max_open:1,enabled:true,color:"#5865F2",footer:"دعم Ader"}];document.getElementById("ticket-editor-host").innerHTML=ticketForm(null);bindTicketEditor();});
  document.querySelectorAll("[data-remove-type]").forEach(b=>b.onclick=()=>{if(document.querySelectorAll("[data-ticket-type]").length<=1)return warn("يجب الإبقاء على نوع واحد على الأقل.");b.closest("[data-ticket-type]")?.remove();});
  async function persist(publish){
    ticketEditor.options=readTicketTypes();
    const gid=s.guild.id,get=id=>document.getElementById(id),val=id=>get(id)?.value||"",check=id=>Boolean(get(id)?.checked);
    const settings={
      enabled:check("ts-enabled"),default_support_role_id:val("ts-role")||null,default_category_id:val("ts-category")||null,
      log_channel_id:val("ts-log")||null,transcript_channel_id:val("ts-transcript")||null,claim_enabled:check("ts-claim"),rating_enabled:check("ts-rating"),
      allow_user_close:check("ts-userclose"),allow_user_reopen:check("ts-userreopen"),allow_member_add:check("ts-add"),allow_member_remove:check("ts-remove"),
      allow_rename:check("ts-rename"),allow_lock:check("ts-lock"),keep_closed:check("ts-keep"),max_open_per_user:Number(val("ts-max")||1),
      channel_name_template:val("ts-template"),delete_after_close_seconds:Number(val("ts-delete")||0)
    };
    await api("/api/guilds/"+gid+"/tickets/settings",{method:"PUT",body:JSON.stringify(settings)});
    const payload={title:val("tp-title"),description:val("tp-description"),channel_id:val("tp-channel")||null,category_id:val("tp-category")||null,
      support_role_id:val("ts-role")||null,image_url:val("tp-image")||null,mode:val("tp-mode"),ticket_description:val("tp-ticket-desc"),
      options:ticketEditor.options,settings:{color:val("tp-color"),thumbnail_url:val("tp-thumb")||null,footer:val("tp-footer"),select_placeholder:"اختر نوع التذكرة",ticket_footer:"دعم Ader"}
    };
    const url="/api/guilds/"+gid+"/tickets/panels"+(ticketEditor.panelId?"/"+ticketEditor.panelId:"");
    const out=await api(url,{method:ticketEditor.panelId?"PUT":"POST",body:JSON.stringify({...payload,publish})});
    ticketEditor.panelId=out.panel.id;ticketEditor.options=out.panel.options||ticketEditor.options;
    warn(publish?"تم حفظ اللوحة ونشرها بنجاح.":"تم حفظ التغييرات بنجاح.");setTimeout(()=>$("#apiWarning").classList.add("hidden"),1800);
    await tickets();
  }
  document.getElementById("ticket-delete")?.addEventListener("click",async()=>{if(!confirm("هل أنت متأكد من حذف لوحة التذاكر؟"))return;try{await api("/api/guilds/"+s.guild.id+"/tickets/panels/"+ticketEditor.panelId,{method:"DELETE"});ticketEditor.panelId=null;await tickets();}catch(e){warn(e.message)}});
  document.getElementById("ticket-save")?.addEventListener("click",()=>persist(false).catch(e=>warn(e.message)));
  document.getElementById("ticket-publish")?.addEventListener("click",()=>persist(true).catch(e=>warn(e.message)));
}
async function welcome(){const d=await api("/api/guilds/"+s.guild.id+"/welcome"),g=d.config||{},m=d.module||{};$("#root").innerHTML=head("Welcome",desc.welcome)+'<div class="cards">'+card("Verification Module",'<label>Enabled<input type="checkbox" id="ver-enabled" '+(m.enabled!==false?"checked":"")+'></label><label>Method<select id="ver-method"><option value="dm" '+(g.verification_method==="dm"?"selected":"")+'>DM</option><option value="channel" '+(g.verification_method==="channel"?"selected":"")+'>Channel</option></select></label><label>Type<input id="ver-type" value="'+esc(g.verification_type||"button")+'"></label>')+card("Channels & Role",'<label>Verified Role'+selectRoles("ver-role",g.verified_role)+'</label><label>Welcome Channel'+selectChannels("welcome-channel",g.welcome_channel)+'</label><label>Verify Channel'+selectChannels("verify-channel",g.verify_channel)+'</label>')+card("Welcome Message",'<textarea id="welcome-message" rows="7">'+esc(g.welcome_message||"مرحبا {user} 👋")+'</textarea><button class="primary smallbtn" id="save-welcome">حفظ</button>')+'</div>';$("#save-welcome").onclick=async()=>{
  try{
    await api("/api/guilds/"+s.guild.id+"/settings",{method:"PUT",body:JSON.stringify({modules:{verification:{enabled:$("#ver-enabled").checked}},verification:{verified_role:$("#ver-role").value||null,welcome_channel:$("#welcome-channel").value||null,verify_channel:$("#verify-channel").value||null,verification_method:$("#ver-method").value,verification_type:$("#ver-type").value,welcome_message:$("#welcome-message").value}})});
    clearWarn();
    await welcome();
  }catch(e){warn("تعذر حفظ إعدادات الترحيب والتحقق: "+e.message)}
  }}
async function commands(){const d=await api("/api/guilds/"+s.guild.id+"/commands");$("#root").innerHTML=head("Commands",desc.commands)+card("الأوامر",'<div class="list">'+(d.commands||[]).map(c=>'<div><span><b>/'+esc(c.name)+'</b><small>'+esc(c.description||"بدون وصف")+'</small></span><label class="switch"><input type="checkbox" data-command="'+esc(c.name)+'" '+(c.enabled?"checked":"")+'><i></i></label></div>').join("")+'</div>');document.querySelectorAll("[data-command]").forEach(x=>x.onchange=async()=>{try{await api("/api/guilds/"+s.guild.id+"/commands/"+encodeURIComponent(x.dataset.command),{method:"PUT",body:JSON.stringify({enabled:x.checked})})}catch(e){x.checked=!x.checked;warn(e.message)}})}
async function shortcuts(){const d=await api("/api/guilds/"+s.guild.id+"/shortcuts");$("#root").innerHTML=head("Shortcuts",desc.shortcuts)+card("الاختصارات",'<div class="list">'+(d.shortcuts||[]).map(x=>'<div class="shortcut-row"><span><b>'+esc(x.label)+'</b><small>'+esc(x.name)+'</small></span><input data-alias="'+esc(x.name)+'" value="'+esc(x.alias||"")+'"><label class="switch"><input type="checkbox" data-shortcut="'+esc(x.name)+'" '+(x.enabled?"checked":"")+'><i></i></label><button class="action" data-save-shortcut="'+esc(x.name)+'">حفظ</button></div>').join("")+'</div>');document.querySelectorAll("[data-save-shortcut]").forEach(b=>b.onclick=async()=>{const n=b.dataset.saveShortcut;try{await api("/api/guilds/"+s.guild.id+"/shortcuts/"+encodeURIComponent(n),{method:"PUT",body:JSON.stringify({alias:document.querySelector("[data-alias='"+CSS.escape(n)+"']").value,enabled:document.querySelector("[data-shortcut='"+CSS.escape(n)+"']").checked})});b.textContent="تم"}catch(e){warn(e.message)}})}
async function teams(){const [d,ts]=await Promise.all([api("/api/guilds/"+s.guild.id+"/teams"),api("/api/guilds/"+s.guild.id+"/teams/settings")]);const cfg=ts.settings||{};$("#root").innerHTML=head("Teams",desc.teams)+'<div class="cards">'+card("Team Settings",'<label>Coach Role'+selectRoles("coach-role",cfg.coach_role_id)+'</label><label>Max Players<input id="max-players" type="number" min="1" max="200" value="'+(cfg.max_players||15)+'"></label><button class="primary smallbtn" id="save-team">حفظ</button>')+card("Verified Teams",'<div class="list">'+(d.teams||[]).map(x=>'<div><b>'+esc(x.emoji||"👥")+' '+esc(x.name)+'</b><small>'+esc(x.team_type)+' · '+x.players+' players · role '+x.role_id+'</small></div>').join("")+'</div>')+'</div>';$("#save-team").onclick=async()=>{
  try{
    await api("/api/guilds/"+s.guild.id+"/teams/settings",{method:"PUT",body:JSON.stringify({coach_role_id:$("#coach-role").value||null,max_players:Number($("#max-players").value)})});
    clearWarn();
    await teams();
  }catch(e){warn("تعذر حفظ إعدادات الفرق: "+e.message)}
  }}
function appQuestionsFromDOM(){
  return Array.from(document.querySelectorAll("[data-app-question]")).map(row=>({
    label:row.querySelector("[data-app-q-label]").value.trim(),
    required:row.querySelector("[data-app-q-required]").checked,
    paragraph:row.querySelector("[data-app-q-paragraph]").value==="true"
  })).filter(q=>q.label);
}
function renderAppQuestions(){
  const host=$("#app-question-list");
  if(!host)return;
  const questions=appEditor.draft?.questions||[];
  host.innerHTML=questions.map((q,i)=>
    '<div class="application-question" data-app-question="'+i+'">'+
      '<span class="question-number">'+String(i+1).padStart(2,"0")+'</span>'+
      '<input data-app-q-label maxlength="45" value="'+esc(q.label||"")+'" placeholder="اكتب السؤال هنا">'+
      '<select data-app-q-paragraph><option value="false" '+(!q.paragraph?"selected":"")+'>إجابة قصيرة</option><option value="true" '+(q.paragraph?"selected":"")+'>إجابة مطولة</option></select>'+
      '<label class="question-required"><input type="checkbox" data-app-q-required '+(q.required!==false?"checked":"")+'> مطلوب</label>'+
      '<button type="button" class="action danger" data-app-q-remove="'+i+'">حذف</button>'+
    '</div>'
  ).join("");
  host.querySelectorAll("[data-app-q-remove]").forEach(button=>button.onclick=()=>{
    const items=appQuestionsFromDOM();
    items.splice(Number(button.dataset.appQRemove),1);
    appEditor.draft.questions=items;
    renderAppQuestions();
  });
}
function applicationPanelPayload(){
  return {
    title:$("#app-title").value.trim(),
    description:$("#app-description").value.trim(),
    button_label:$("#app-button-label").value.trim(),
    button_emoji:$("#app-button-emoji").value.trim(),
    button_style:$("#app-button-style").value,
    image:$("#app-image").value.trim(),
    results_channel_id:$("#app-results-channel").value||null,
    accept_role_id:$("#app-accept-role").value||null,
    reject_role_id:$("#app-reject-role").value||null,
    questions:appQuestionsFromDOM()
  };
}
async function saveApplicationPanel(){
  const payload=applicationPanelPayload();
  await api("/api/guilds/"+s.guild.id+"/applications/panels/"+appEditor.panelNumber,{method:"PUT",body:JSON.stringify(payload)});
  return payload;
}
async function applications(){
  const data=await api("/api/guilds/"+s.guild.id+"/applications");
  appEditor.panels=data.panels||[];
  appEditor.applications=data.applications||[];
  const panel=appEditor.panels.find(x=>Number(x.number)===Number(appEditor.panelNumber))||appEditor.panels[0]||{};
  appEditor.panelNumber=Number(panel.number||1);
  appEditor.draft=JSON.parse(JSON.stringify(panel));
  const pending=appEditor.applications.filter(x=>x.status==="pending").length;
  const accepted=appEditor.applications.filter(x=>x.status==="accepted").length;
  const rejected=appEditor.applications.filter(x=>x.status==="rejected").length;
  const statusLabel={pending:"قيد المراجعة",accepted:"مقبول",rejected:"مرفوض"};
  const applicationRows=appEditor.applications.length?appEditor.applications.map(item=>{
    const answers=Array.isArray(item.answers)?item.answers:[];
    const answerText=answers.map((answer,i)=>(i+1)+". "+String(answer||"")).join("\n");
    const reviewButtons=item.status==="pending"
      ? '<div class="application-review-actions"><button class="primary smallbtn" data-app-review="'+item.id+'" data-status="accepted">✅ قبول</button><button class="action danger" data-app-review="'+item.id+'" data-status="rejected">رفض</button></div>'
      : '<span class="application-status '+esc(item.status)+'">'+esc(statusLabel[item.status]||item.status)+'</span>';
    return '<article class="application-entry">'+
      '<div class="application-entry-top"><div><b>طلب #'+item.id+'</b><small>العضو ID '+item.user_id+' · اللوحة '+item.panel+'</small></div>'+
      '<time>'+esc(new Date(Number(item.created_at||0)*1000).toLocaleString())+'</time></div>'+
      '<details><summary>عرض الإجابات ('+answers.length+')</summary><pre>'+esc(answerText||"لا توجد إجابات")+'</pre></details>'+
      '<div class="application-entry-bottom">'+reviewButtons+'</div></article>';
  }).join(""):'<div class="empty small">لا توجد طلبات تقديم بعد. انشر لوحة التقديم وسيتم عرض الطلبات هنا.</div>';
  $("#root").innerHTML=head("تقديم الإدارة",desc.applications)+
    '<div class="stats application-stats">'+stat("طلبات قيد المراجعة",pending,"◷")+stat("طلبات مقبولة",accepted,"✓")+stat("طلبات مرفوضة",rejected,"×")+stat("إجمالي الطلبات",appEditor.applications.length,"▤")+'</div>'+
    '<div class="application-layout">'+
      '<section class="card application-editor">'+
        '<div class="card-head"><b>إعداد لوحة التقديم</b><span class="system-pill">لوحات متعددة</span></div>'+
        '<div class="card-body">'+
          '<div class="form-grid"><label>اختيار اللوحة<select id="app-panel-number">'+[1,2,3].map(n=>'<option value="'+n+'" '+(n===appEditor.panelNumber?"selected":"")+'>لوحة '+n+'</option>').join("")+'</select></label>'+
          '<label>عنوان اللوحة<input id="app-title" maxlength="256" value="'+esc(panel.title||"")+'"></label></div>'+
          '<label>وصف التقديم<textarea id="app-description" rows="3" maxlength="4000">'+esc(panel.description||"")+'</textarea></label>'+
          '<div class="form-grid"><label>اسم الزر<input id="app-button-label" maxlength="80" value="'+esc(panel.button_label||"ابدأ التقديم")+'"></label>'+
          '<label>إيموجي الزر<input id="app-button-emoji" maxlength="64" value="'+esc(panel.button_emoji||"📝")+'"></label>'+
          '<label>لون الزر<select id="app-button-style">'+
            '<option value="primary" '+(panel.button_style==="primary"?"selected":"")+'>أزرق / بنفسجي</option>'+
            '<option value="success" '+(panel.button_style==="success"?"selected":"")+'>أخضر</option>'+
            '<option value="secondary" '+(panel.button_style==="secondary"?"selected":"")+'>رمادي</option>'+
            '<option value="danger" '+(panel.button_style==="danger"?"selected":"")+'>أحمر</option>'+
          '</select></label>'+
          '<label>رابط صورة اللوحة<input id="app-image" type="url" placeholder="https://..." value="'+esc(panel.image||"")+'"></label></div>'+
          '<div class="form-grid"><label>قناة استقبال الطلبات'+selectChannels("app-results-channel",panel.results)+'</label>'+
          '<label>رتبة المقبولين'+selectRoles("app-accept-role",panel.accept_role)+'</label>'+
          '<label>رتبة المرفوضين'+selectRoles("app-reject-role",panel.reject_role)+'</label>'+
          '<label>قناة نشر اللوحة'+selectChannels("app-publish-channel",panel.published_channel_id)+'</label></div>'+
          '<div class="application-questions-head"><div><b>أسئلة التقديم</b><small>من 1 إلى 25 سؤالاً. إجابات مطولة أو قصيرة.</small></div><button class="action" id="app-add-question">＋ إضافة سؤال</button></div>'+
          '<div id="app-question-list" class="application-question-list"></div>'+
          '<div class="application-editor-actions"><button class="primary smallbtn" id="app-save">حفظ الإعدادات</button><button class="action" id="app-publish">نشر / تحديث اللوحة</button></div>'+
        '</div>'+
      '</section>'+
      '<section class="card application-requests"><div class="card-head"><b>طلبات الإدارة</b><span class="system-pill">'+appEditor.applications.length+' طلب</span></div><div class="card-body"><div class="application-entry-list">'+applicationRows+'</div></div></section>'+
    '</div>';
  renderAppQuestions();
  $("#app-panel-number").onchange=()=>{appEditor.panelNumber=Number($("#app-panel-number").value);applications()};
  $("#app-add-question").onclick=()=>{
    const questions=appQuestionsFromDOM();
    if(questions.length>=25)return warn("الحد الأقصى هو 25 سؤالاً.");
    questions.push({label:"سؤال جديد",required:true,paragraph:false});
    appEditor.draft.questions=questions;
    renderAppQuestions();
  };
  $("#app-save").onclick=async()=>{
    try{await saveApplicationPanel();clearWarn();await applications();warn("تم حفظ إعدادات لوحة التقديم بنجاح.");}
    catch(e){warn("تعذر حفظ التقديم: "+e.message)}
  };
  $("#app-publish").onclick=async()=>{
    try{
      await saveApplicationPanel();
      const channelId=$("#app-publish-channel").value;
      if(!channelId)throw Error("اختر قناة نشر اللوحة أولاً.");
      const result=await api("/api/guilds/"+s.guild.id+"/applications/panels/"+appEditor.panelNumber+"/publish",{method:"POST",body:JSON.stringify({channel_id:channelId})});
      await applications();
      warn("تم نشر لوحة التقديم بنجاح. رابط الرسالة: "+result.message_url);
    }catch(e){warn("تعذر نشر لوحة التقديم: "+e.message)}
  };
  document.querySelectorAll("[data-app-review]").forEach(button=>button.onclick=async()=>{
    const applicationId=Number(button.dataset.appReview);
    const status=button.dataset.status;
    let reason="";
    if(status==="rejected"){
      const value=window.prompt("سبب رفض التقديم (اختياري):");
      if(value===null)return;
      reason=value;
    }
    try{
      await api("/api/guilds/"+s.guild.id+"/applications/"+applicationId+"/review",{method:"POST",body:JSON.stringify({status,reason})});
      await applications();
      warn("تم حفظ قرار مراجعة الطلب.");
    }catch(e){warn("تعذرت مراجعة الطلب: "+e.message)}
  });
}
async function logs(){
  const [data,config]=await Promise.all([
    api("/api/guilds/"+s.guild.id+"/logs"),
    api("/api/guilds/"+s.guild.id+"/logs/settings")
  ]);
  const settings=config.settings||{},categories=settings.categories||{};
  const names={messages:"الرسائل",members:"الأعضاء",moderation:"الإشراف",channels:"القنوات",roles:"الرتب"};
  const labels={
    "audit.message_deleted":"حذف رسالة","audit.message_edited":"تعديل رسالة",
    "audit.member_joined":"انضمام عضو","audit.member_left":"مغادرة عضو",
    "audit.member_banned":"حظر عضو","audit.member_unbanned":"إلغاء حظر عضو",
    "audit.channel_created":"إنشاء قناة","audit.channel_updated":"تعديل قناة","audit.channel_deleted":"حذف قناة",
    "audit.role_created":"إنشاء رتبة","audit.role_updated":"تعديل رتبة","audit.role_deleted":"حذف رتبة",
    "ticket.created":"إنشاء تذكرة","ticket.closed":"إغلاق تذكرة","ticket.reopened":"إعادة فتح تذكرة",
    "ticket.claim":"استلام تذكرة","ticket.deleted":"حذف تذكرة","ticket.snapshot":"سجل تذكرة",
    "application.submitted":"إرسال طلب تقديم","application.reviewed":"مراجعة طلب تقديم"
  };
  const items=(data.logs||[]).map(item=>{
    const type=String(item.type||"حدث"),body=item.data||{};
    const title=labels[type]||type.replace(/[._]/g," ");
    const description=Object.entries(body).slice(0,5).map(([key,value])=>'<span><b>'+esc(key)+'</b> '+esc(typeof value==="object"?JSON.stringify(value):String(value??""))+'</span>').join("");
    return '<article class="log-event"><div class="log-event-icon">◈</div><div class="log-event-main"><b>'+esc(title)+'</b><div class="log-event-meta">'+esc(type)+' · '+esc(new Date(Number(item.timestamp||0)*1000).toLocaleString())+'</div><div class="log-event-data">'+description+'</div></div></article>';
  }).join("");
  $("#root").innerHTML=head("سجلات السيرفر",desc.logs)+
    '<div class="application-layout logs-layout">'+
      '<section class="card"><div class="card-head"><b>إعدادات السجلات</b><span class="system-pill">تسجيل تلقائي</span></div><div class="card-body">'+
        '<label>قناة إرسال السجلات'+selectChannels("logs-channel",settings.channel_id)+'</label>'+
        '<div class="log-category-grid">'+Object.entries(names).map(([key,label])=>'<label><input type="checkbox" data-log-category="'+key+'" '+(categories[key]!==false?"checked":"")+'> '+esc(label)+'</label>').join("")+'</div>'+
        '<button class="primary smallbtn" id="save-logs">حفظ إعدادات السجلات</button>'+
        '<p class="muted">تُحفظ الأحداث في قاعدة البيانات وتظهر هنا حتى إذا لم يتم تعيين قناة Discord. القناة اختيارية لإرسال نسخة منها داخل السيرفر.</p>'+
      '</div></section>'+
      '<section class="card"><div class="card-head"><b>آخر الأحداث</b><span class="system-pill">'+(data.logs||[]).length+' حدث</span></div><div class="card-body"><div class="log-event-list">'+(items||'<div class="empty small">لا توجد أحداث مسجلة بعد.</div>')+'</div></div></section>'+
    '</div>';
  $("#save-logs").onclick=async()=>{
    const payload={
      channel_id:$("#logs-channel").value||null,
      categories:Object.fromEntries(Array.from(document.querySelectorAll("[data-log-category]")).map(x=>[x.dataset.logCategory,x.checked]))
    };
    try{await api("/api/guilds/"+s.guild.id+"/logs/settings",{method:"PUT",body:JSON.stringify(payload)});await logs();warn("تم حفظ إعدادات السجلات.");}
    catch(e){warn("تعذر حفظ إعدادات السجلات: "+e.message)}
  };
}
async function premium(){
  const d=await api("/api/guilds/"+s.guild.id+"/premium");
  const active=Boolean(d.active);
  const b=d.linked_bot||{};
  const limit=v=>v===null||v===undefined?"غير محدود":Number(v).toLocaleString();
  const featureHtml=(d.features||[]).map(f=>'<div class="premium-feature"><span><b>'+esc(f.name)+'</b><small>'+esc(f.description||"")+'</small></span><strong>'+((f.enabled&&active)?"✓":"—")+'</strong></div>').join("");
  let linkedHtml="";
  if(!active){
    linkedHtml=card("Premium مطلوب",'<div class="empty small">ربط البوت وتخصيص هويته يحتاجان سيرفر Premium فعال.</div>');
  }else if(!b.configured){
    linkedHtml=card("ربط بوتك الخاص",
      '<p class="muted">دخل توكن بوت Discord ديالك. Ader ما كيخزنوش كنص عادي: كيتخزن مشفراً في الداتا على السيرفر، وما كنرجعوش للواجهة من بعد الحفظ.</p>'+
      '<label>توكن البوت<input id="lb-token" type="password" autocomplete="new-password" placeholder="توكن Discord Bot"></label>'+
      '<div class="form-grid">'+
      '<label>اسم البوت<input id="lb-name" maxlength="32" placeholder="اسم البوت"></label>'+
      '<label>Watching<input id="lb-activity" maxlength="128" value="Managing your community"></label>'+
      '<label>نوع الحالة<select id="lb-type"><option value="watching">Watching</option><option value="playing">Playing</option><option value="listening">Listening</option><option value="streaming">Streaming</option></select></label>'+
      '</div>'+
      '<label>رابط الصورة<input id="lb-avatar" type="url" placeholder="https://..."></label>'+
      '<label>رابط البنر<input id="lb-banner" type="url" placeholder="https://..."></label>'+
      '<label>Bio — محفوظ داخل Ader فقط، ما كيبدلش البايو العام<input id="lb-bio" maxlength="1900" placeholder="نبذة البوت"></label>'+
      '<button class="primary smallbtn" id="lb-link">ربط وتشغيل البوت</button>'
    );
  }else{
    const invite=b.bot_user_id?"https://discord.com/oauth2/authorize?client_id="+encodeURIComponent(b.bot_user_id)+"&scope=bot%20applications.commands":"";
    linkedHtml=card("Linked Bot",
      '<div class="kv"><span>Bot ID</span><b>'+esc(b.bot_user_id||"—")+'</b></div>'+
      '<div class="kv"><span>الحالة</span><b class="'+(b.running?"on":"off")+'">'+(b.running?"Online":"Stopped")+'</b></div>'+
      (b.last_error?'<div class="warning">'+esc(b.last_error)+'</div>':'')+
      (invite?'<a class="action" href="'+invite+'" target="_blank" rel="noopener">دعوة البوت للسيرفر</a>':'')+
      '<div class="form-grid">'+
      '<label>اسم البوت<input id="lb-name" maxlength="32" value="'+esc(b.bot_name||"")+'"></label>'+
      '<label>Watching<input id="lb-activity" maxlength="128" value="'+esc(b.activity_text||"Managing your community")+'"></label>'+
      '<label>نوع الحالة<select id="lb-type"><option value="watching" '+(b.activity_type==="watching"?"selected":"")+'>Watching</option><option value="playing" '+(b.activity_type==="playing"?"selected":"")+'>Playing</option><option value="listening" '+(b.activity_type==="listening"?"selected":"")+'>Listening</option><option value="streaming" '+(b.activity_type==="streaming"?"selected":"")+'>Streaming</option></select></label>'+
      '</div>'+
      '<label>رابط الصورة<input id="lb-avatar" type="url" value="'+esc(b.avatar_url||"")+'" placeholder="https://..."></label>'+
      '<label>رابط البنر<input id="lb-banner" type="url" value="'+esc(b.banner_url||"")+'" placeholder="https://..."></label>'+
      '<label>Bio — محفوظ داخل Ader فقط، ما كيبدلش البايو العام<input id="lb-bio" maxlength="1900" value="'+esc(b.bio||"")+'"></label>'+
      '<p class="muted">التوكن مخفي ومخزن مشفراً؛ ما تحتاجش تعاود تدخلو لتعديل البروفايل.</p>'+
      '<div class="quick"><button class="primary smallbtn" id="lb-profile">حفظ البروفايل</button><button class="action" id="lb-'+(b.running?"stop":"start")+'">'+(b.running?"إيقاف البوت":"تشغيل البوت")+'</button><button class="action danger" id="lb-delete">فك الربط</button></div>'
    );
  }
  $("#root").innerHTML=head("Ader Premium",desc.premium)+
    '<div class="stats">'+stat("الخطة",active?(d.plan||"premium").toUpperCase():"FREE","⭐")+stat("الحالة",active?"ACTIVE":"FREE",active?"✓":"—")+stat("تنتهي",d.expires_at?new Date(d.expires_at*1000).toLocaleDateString():"—","⌛")+stat("Linked Bot",b.configured?(b.running?"ONLINE":"STOPPED"):"NONE","🤖")+'</div>'+
    '<div class="cards">'+card("Premium Features",'<div class="premium-features">'+featureHtml+'</div>')+linkedHtml+'</div>'+
    (active?card("ملاحظة مهمة",'<div class="empty small">Ader والـLinked Bot كيستعملو نفس قاعدة البيانات ونفس إعدادات السيرفر والاقتصاد والتذاكر والإحصائيات. تغيير الإعدادات من أي جهة كيبان بنفس الشكل للجهة الأخرى.</div>'):card("Ader Premium",'<div class="empty small">السيرفر حالياً خارج Premium. فعّل الاشتراك باش تفتح Linked Bot والتخصيصات المتقدمة.</div>'));
  if(!active)return;
  const collectProfile=()=>({
    bot_name:$("#lb-name")?.value||"",
    avatar_url:$("#lb-avatar")?.value||"",
    banner_url:$("#lb-banner")?.value||"",
    bio:$("#lb-bio")?.value||"",
    activity_text:$("#lb-activity")?.value||"Managing your community",
    activity_type:$("#lb-type")?.value||"watching"
  });
  document.getElementById("lb-link")?.addEventListener("click",async()=>{
    const token=$("#lb-token")?.value||"";
    if(!token)return warn("دخل توكن البوت أولاً.");
    try{await api("/api/guilds/"+s.guild.id+"/premium/linked-bot",{method:"POST",body:JSON.stringify({...collectProfile(),token})});await premium()}
    catch(e){warn(e.message)}
  });
  document.getElementById("lb-profile")?.addEventListener("click",async()=>{
    try{await api("/api/guilds/"+s.guild.id+"/premium/linked-bot",{method:"PUT",body:JSON.stringify(collectProfile())});await premium()}
    catch(e){warn(e.message)}
  });
  document.getElementById("lb-start")?.addEventListener("click",async()=>{
    try{await api("/api/guilds/"+s.guild.id+"/premium/linked-bot/start",{method:"POST"});await premium()}
    catch(e){warn(e.message)}
  });
  document.getElementById("lb-stop")?.addEventListener("click",async()=>{
    try{await api("/api/guilds/"+s.guild.id+"/premium/linked-bot/stop",{method:"POST"});await premium()}
    catch(e){warn(e.message)}
  });
  document.getElementById("lb-delete")?.addEventListener("click",async()=>{
    if(!confirm("واش متأكد بغيتي تفك ربط البوت؟"))return;
    try{await api("/api/guilds/"+s.guild.id+"/premium/linked-bot",{method:"DELETE"});await premium()}
    catch(e){warn(e.message)}
  });
}

function security(){$("#root").innerHTML=head("Security",desc.security)+'<div class="cards">'+card("Session",'<div class="kv"><span>User</span><b>'+esc(s.user?.username||"Discord User")+'</b></div><div class="kv"><span>Managed Servers</span><b>'+s.guilds.length+'</b></div>')+card("OAuth",'<div class="empty small">تسجيل الدخول يمر عبر نفس Cloudflare origin، مع state validation وSession Cookie آمن.</div><a class="btn primary" href="/login?force=1">إعادة تسجيل الدخول بواسطة Discord</a>')+'</div>'}
async function settings(){
  const [d,core]=await Promise.all([
    api("/api/guilds/"+s.guild.id+"/settings"),
    api("/api/guilds/"+s.guild.id+"/core-settings")
  ]);
  const m=d.modules||{},s2=core.settings||{},a=s2.automod||{},rf=a.reaction_filter||{},ar=s2.autorole||{},l=s2.levels||{},n=s2.antinuke||{},logsCfg=s2.logs||{};
  const words=(a.words||[]).join(", ");
  const reactionEmojis=(rf.emojis||[]).join(", ");
  $("#root").innerHTML=head("Settings",desc.settings)+
    '<div class="cards">'+
      card("Modules",'<div class="list">'+["moderation","verification","analytics","leveling","roles","tickets","games"].map(nm=>'<div><b>'+nm+'</b><label class="switch"><input type="checkbox" data-module="'+nm+'" '+(m[nm]?.enabled!==false?"checked":"")+'><i></i></label></div>').join("")+'</div>')+
      card("السجلات",'<label>قناة السجلات'+selectChannels("core-log",logsCfg.channel)+'</label><p class="muted">يسجل أحداث Core المهمة في قناة واحدة.</p>')+
      card("AutoMod",'<label class="switchline">تفعيل <input type="checkbox" id="core-auto" '+(a.enabled!==false?"checked":"")+'></label><label>حد الرسائل<input id="core-msg" type="number" min="3" max="50" value="'+(a.messages??6)+'"></label><label>ثواني النافذة<input id="core-win" type="number" min="2" max="60" value="'+(a.window??5)+'"></label><label>التحذير/Timeout بالدقائق<input id="core-time" type="number" min="1" max="40320" value="'+(a.timeout??5)+'"></label><label>منع الروابط<input type="checkbox" id="core-links" '+(a.links?"checked":"")+'></label><label>الكلمات المحظورة<input id="core-words" value="'+esc(words)+'" placeholder="كلمة1, كلمة2"></label>')+
      card("منع التفاعلات",'<label class="switchline">تفعيل <input type="checkbox" id="core-reaction-on" '+(rf.enabled?"checked":"")+'></label><label>الإيموجيات الممنوعة<input id="core-reactions" value="'+esc(reactionEmojis)+'" placeholder="😀, 😡, <:emoji:123456789>, <a:emoji:123456789>"></label><p class="muted">كتب الإيموجي نفسه ماشي سميتو، وفصل بين كل إيموجي بفاصلة. التكرار كيتحيد تلقائياً.</p><label>مدة Timeout بالدقائق<input id="core-reaction-time" type="number" min="1" max="40320" value="'+(rf.timeout??5)+'"></label>')+
      card("AutoRole",'<label>الرتبة'+selectRoles("core-role",ar.role)+'</label><label>تفعيل <input type="checkbox" id="core-role-on" '+(ar.enabled?"checked":"")+'></label>')+
      card("Levels",'<label>تفعيل <input type="checkbox" id="core-levels" '+(l.enabled!==false?"checked":"")+'></label><label>Cooldown بالثواني<input id="core-lvl-cd" type="number" min="5" max="3600" value="'+(l.cooldown??45)+'"></label><label>XP الأدنى<input id="core-xp-min" type="number" min="1" max="100" value="'+(l.min??8)+'"></label><label>XP الأقصى<input id="core-xp-max" type="number" min="1" max="100" value="'+(l.max??14)+'"></label>')+
      card("Anti-Nuke",'<label>تفعيل <input type="checkbox" id="core-nuke" '+(n.enabled!==false?"checked":"")+'></label><label>عدد العمليات<input id="core-nuke-th" type="number" min="2" max="20" value="'+(n.threshold??4)+'"></label><label>النافذة بالثواني<input id="core-nuke-win" type="number" min="3" max="120" value="'+(n.window??10)+'"></label><label>الإجراء<select id="core-nuke-act"><option value="timeout" '+(n.action==="timeout"?"selected":"")+'>Timeout</option><option value="ban" '+(n.action==="ban"?"selected":"")+'>Ban</option></select></label>')+
    '</div>'+
    '<button class="primary smallbtn" id="save-core">حفظ إعدادات الأنظمة</button>';
  document.querySelectorAll("[data-module]").forEach(x=>x.onchange=async()=>{
    try{await api("/api/guilds/"+s.guild.id+"/module-enabled/"+x.dataset.module,{method:"PUT",body:JSON.stringify({enabled:x.checked})})}
    catch(e){x.checked=!x.checked;warn(e.message)}
  });
  $("#save-core").onclick=async()=>{
    try{
      const payload={
        logs:{channel:$("#core-log").value||null},
        automod:{
          enabled:$("#core-auto").checked,
          messages:Number($("#core-msg").value),
          window:Number($("#core-win").value),
          timeout:Number($("#core-time").value),
          links:$("#core-links").checked,
          words:$("#core-words").value.split(",").map(x=>x.trim()).filter(Boolean),
          reaction_filter:{
            enabled:$("#core-reaction-on").checked,
            timeout:Number($("#core-reaction-time").value),
            emojis:[...new Set($("#core-reactions").value.split(",").map(x=>x.trim()).filter(Boolean))]
          }
        },
        autorole:{enabled:$("#core-role-on").checked,role:$("#core-role").value||null},
        levels:{
          enabled:$("#core-levels").checked,
          cooldown:Number($("#core-lvl-cd").value),
          min:Number($("#core-xp-min").value),
          max:Number($("#core-xp-max").value)
        },
        antinuke:{
          enabled:$("#core-nuke").checked,
          threshold:Number($("#core-nuke-th").value),
          window:Number($("#core-nuke-win").value),
          action:$("#core-nuke-act").value
        }
      };
      await api("/api/guilds/"+s.guild.id+"/core-settings",{method:"PUT",body:JSON.stringify(payload)});
      await settings();
    }catch(e){warn("تعذر حفظ الإعدادات: "+e.message)}
  };
}
function giveaways(){const rows=s.overview||{};$("#root").innerHTML=head("Giveaways",desc.giveaways)+card("Status",'<div class="empty small">قاعدة البيانات فيها جدول giveaways، ولكن main.py حالياً ما كيحملش cog مستقل ديال giveaways؛ لذلك هاد القسم ما غاديش يوهمك بوجود إنشاء غير مربوط.</div>')}
function bindCommon(){document.querySelectorAll("[data-go]").forEach(b=>b.onclick=()=>{s.page=b.dataset.go;render()});document.querySelectorAll("[data-guild]").forEach(b=>b.onclick=()=>pick(b.dataset.guild))}
async function boot(){
  try{
    const me=await api("/api/me");
    if(!me.logged_in)return showLogin();
    s.user=me.user;

    const g=await api("/api/guilds");
    s.guilds=(g.guilds||[]).filter(x=>x&&x.id);

    showApp();
    if(!s.guilds.length){
      $("#root").innerHTML='<div class="empty">ما كاين حتى سيرفر متاح للإدارة ومثبت فيه Ader.</div>';
      return;
    }

    const saved=localStorage.getItem("ader_selected_guild_id");
    const savedGuild=s.guilds.find(x=>String(x.id)===String(saved));
    s.guild=savedGuild||s.guilds[0];

    $("#user").textContent=(s.user.username||"U")[0];
    $("#guildSelect").innerHTML=s.guilds.map(x=>'<option value="'+x.id+'">'+esc(x.name)+'</option>').join("");
    $("#guildSelect").value=String(s.guild.id);
    $("#guildSelect").onchange=()=>pick($("#guildSelect").value);

    await loadGuild();
    await render();
  }catch(e){
    if(e.message==="AUTH")showLogin();
    else{
      showLogin();
      loginError("تعذر تشغيل لوحة Ader: "+e.message);
    }
  }
}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{s.page=b.dataset.page;$("#sidebar").classList.remove("open");render()});$("#loginBtn").onclick=()=>location.assign("/login?force=1");$("#logoutBtn").onclick=()=>location.href=API+"/logout";$("#refresh").onclick=()=>loadGuild().then(render);const savedTheme=localStorage.getItem("ano_dashboard_theme");if(savedTheme==="light")document.body.classList.add("light");$("#theme").onclick=()=>{document.body.classList.toggle("light");localStorage.setItem("ano_dashboard_theme",document.body.classList.contains("light")?"light":"dark")};$("#menu").onclick=()=>$("#sidebar").classList.toggle("open");boot();
})();