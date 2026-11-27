(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const roles = [
    ['session','Session','selection','Agent name'], ['pr','Pull request','selection','PR number'],
    ['question','Question','selection','Blue'], ['request','Request','selection','Orange'],
    ['notice','Notice','selection','Gray'], ['option','Option','selection','Answer choice'],
    ['action','Action','selection','Item command'],
    ['success','Success','accents','Passing check'], ['error','Error','accents','Failed check'],
    ['warning','Warning','accents','Pending check'], ['ready','Ready','accents','Review state'],
    ['prCount','PR count','accents','Session count'], ['fixer','Fixer','accents','Terminal blue'],
    ['branch','Branch','accents','Terminal cyan'],
    ['background','Background','canvas','Terminal canvas'], ['foreground','Default text','canvas','Unstyled text'],
    ['muted','Muted text','canvas','Age and details'], ['divider','Divider','canvas','Panel separator'],
    ['hint','Hints','canvas','Bottom shortcuts'], ['prompt','Prompt','canvas','Input symbol'],
    ['userText','Your message','canvas','Text'], ['userBg','Your message fill','canvas','Background'],
    ['agentText','Agent message','canvas','Conversation text'], ['toolText','Tool activity','canvas','Tool details']
  ].map(([id,label,group,note]) => ({id,label,group,note,selectable:group !== 'canvas'}));
  const byId = Object.fromEntries(roles.map(r => [r.id,r]));
  const descriptions = {selection:'Select the text, keep its color family. Normal and selected colors are independent.',accents:'Status colors and terminal accents, including their selected versions.',canvas:'Sample terminal colors and conversation styling. These do not have a selected state.'};
  const rgb = hex => hex.match(/[a-f\d]{2}/gi).map(v => parseInt(v,16));
  const hex = channels => '#' + channels.map(v => Math.round(v).toString(16).padStart(2,'0')).join('');
  const blend = (a,b,t) => hex(rgb(a).map((v,i) => v * (1-t) + rgb(b)[i]*t));
  const brighter = color => hex(rgb(color).map(v => Math.floor((v*3+255)/4)));
  const clone = value => JSON.parse(JSON.stringify(value));
  const defaults = theme => {
    const dark = theme === 'dark', fg = dark ? '#c7c7c7' : '#252525';
    const base = {session:fg,pr:fg,question:dark?'#b1b9f9':'#5769f7',request:dark?'#ffbe8c':'#b45a28',notice:dark?'#999999':'#666666',option:fg,action:fg,success:dark?'#4eba65':'#2c7a39',error:dark?'#ff6b80':'#ab2b3f',warning:dark?'#ffc107':'#966c1e',ready:dark?'#b1b9f9':'#5769f7',prCount:'#d77757',fixer:'#0000ee',branch:'#00cdcd',background:dark?'#1c1c1c':'#f6f5f0',foreground:fg,muted:dark?'#505050':'#afafaf',divider:dark?'#454941':'#d0d2c8',hint:dark?'#505050':'#afafaf',prompt:fg,userText:'#ffffff',userBg:'#404040',agentText:fg,toolText:dark?'#505050':'#afafaf'};
    const plain = new Set(['session','pr','option','action']);
    return {softness:0,colors:Object.fromEntries(roles.map(r => [r.id,{normal:base[r.id],...(r.selectable?{selected:plain.has(r.id)?(dark?'#ffffff':'#404040'):r.id==='fixer'?'#5c5cff':r.id==='branch'?'#00ffff':brighter(base[r.id])}:{})}]))};
  };
  let state = {theme:'dark',view:'panel',quantize:true,bold:true,fontSize:15,active:'request',selection:'request:Request',group:'selection',palettes:{dark:defaults('dark'),light:defaults('light')}};
  const history = [], storageKey = 'projector-console-colors-v1';
  const effective = (id,kind='normal',theme=state.theme) => {
    const palette = state.palettes[theme], color = palette.colors[id];
    return kind === 'selected' && color.selected ? blend(color.selected,color.normal,palette.softness/100) : color.normal;
  };
  function exported() {
    return {schema:1,name:'Projector console palette',themes:Object.fromEntries(['dark','light'].map(theme => [theme,Object.fromEntries(roles.map(r => [r.id,{normal:effective(r.id,'normal',theme),...(r.selectable?{selected:effective(r.id,'selected',theme)}:{})}]))])),preview:{theme:state.theme,terminalColors:state.quantize?'256':'true-color',bold:state.bold,fontSize:state.fontSize}};
  }
  function parse(value) {
    if (!value || value.schema !== 1 || !value.themes) throw Error('Use a palette exported by this editor (schema 1).');
    const palettes = {};
    for (const theme of ['dark','light']) {
      const colors = {};
      for (const role of roles) {
        const source = value.themes[theme]?.[role.id], color = {};
        for (const kind of role.selectable?['normal','selected']:['normal']) {
          if (typeof source?.[kind] !== 'string' || !/^#[a-f\d]{6}$/i.test(source[kind])) throw Error(`Missing or invalid ${theme} ${role.label.toLowerCase()} ${kind} color.`);
          color[kind] = source[kind].toLowerCase();
        }
        colors[role.id] = color;
      }
      palettes[theme] = {softness:0,colors};
    }
    const preview = value.preview || {};
    return {palettes,theme:preview.theme==='light'?'light':'dark',quantize:preview.terminalColors!=='true-color',bold:preview.bold!==false,fontSize:Number.isFinite(preview.fontSize)?Math.max(12,Math.min(19,preview.fontSize)):15};
  }
  try {
    const shared = new URLSearchParams(location.hash.slice(1)).get('palette');
    const saved = shared ? JSON.parse(decodeURIComponent(escape(atob(shared)))) : JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved) Object.assign(state,parse(saved));
  } catch { setTimeout(() => toast('Could not restore that palette. Using the current colors.'),200); }
  function remember() { history.push(clone(state)); if (history.length>50) history.shift(); }
  function bake() {
    for (const theme of ['dark','light']) { for (const role of roles) if (role.selectable) state.palettes[theme].colors[role.id].selected=effective(role.id,'selected',theme); state.palettes[theme].softness=0; }
  }
  const cube = [0,95,135,175,215,255], xterm = [];
  for (const r of cube) for (const g of cube) for (const b of cube) xterm.push([r,g,b]);
  for (let n=0;n<24;n++) xterm.push([8+n*10,8+n*10,8+n*10]);
  const nearest = color => { const original=rgb(color); let best=xterm[0], distance=Infinity; for (const candidate of xterm) { const d=candidate.reduce((sum,v,i)=>sum+(v-original[i])**2,0); if(d<distance){best=candidate;distance=d;} } return hex(best); };
  const display = color => state.quantize?nearest(color):color;
  const shown = (id,kind='normal') => display(effective(id,kind));
  const escapeText = value => String(value).replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const styled = (id,text,extra='') => `<span style="color:${shown(id)}" class="${extra}">${escapeText(text)}</span>`;
  const pick = (id,text,extra='',force) => {
    const selection = id + ':' + text, active = force ?? state.selection === selection;
    return `<button type="button" data-focus="${id}" data-selection="${escapeText(selection)}" class="term-select ${extra}" style="color:${shown(id,active?'selected':'normal')};font-weight:${active&&state.bold?700:400}" aria-label="Select ${escapeText(text)}" aria-pressed="${active}">${escapeText(text)}</button>`;
  };
  function panel() {
    return `<div class="term-heading">${styled('foreground','PROJECTOR')} ${styled('muted','3 sessions')}</div><div class="term-directory">${styled('branch','~/projects/workbench')}</div>`+
      [['Build the color editor','2 PRs','now'],['Review the console','1 PR','2m'],['Update the docs','','5m']].map(([name,count,age],i)=>`<div class="term-row"><span class="term-marker">${i===0?'▸':' '}</span>${pick('session',name,'session-name')}${styled('prCount',count,'term-count')}${styled('muted',age,'term-age')}</div>`).join('')+
      `<div class="term-divider"></div>`+
      [['#123','ready','ready','Tune the console colors'],['#124','success','passing','Keep the panel minimal'],['#125','warning','pending','Add keyboard navigation'],['#126','error','failed','Update the preview']].map(([number,role,status,title])=>`<div class="term-row">${pick('pr',number,'term-pr')}${pick(role,status,'term-state')}${styled('foreground',title,'term-title')}</div>`).join('')+
      `<div class="term-row">${styled('muted','  ↳')}${pick('fixer','fix-123')}${pick('branch','console-colors')}</div><div class="term-inbox"><div class="term-row">${pick('request','Request')} ${styled('foreground','Try the new palette','term-title')}</div><div class="term-option">${pick('option','› Looks good')} &nbsp; ${pick('option','Some feedback')}</div><div class="term-action">${pick('action','o open')} ${pick('action','r reply')} ${pick('action','d dismiss')}</div></div><div class="term-row">${pick('question','Question')} ${styled('foreground','Which shade do you prefer?','term-title')}</div><div class="term-row">${pick('notice','Notice')} ${styled('foreground','The preview is ready','term-title')}</div>`;
  }
  function chat() {
    return `<div class="term-heading">${pick('session','Build the color editor')} ${styled('muted','· Claude')}</div><div class="chat-user" style="background:${shown('userBg')};color:${shown('userText')}">Soften the selected colors and keep each color family.</div><div class="chat-agent" style="color:${shown('agentText')}">I’ll keep requests orange, questions blue, and notices gray. The selected text will use a quieter shade of the same color.</div><div class="chat-tool" style="color:${shown('toolText')}">● Read console.py<br>● Updated the preview palette</div><div class="chat-agent" style="color:${shown('agentText')}">The palette is ready to compare. Select a label or adjust the colors alongside this preview.</div><div class="chat-statuses">${pick('success','✓ checks pass')} ${pick('warning','● waiting')} ${pick('error','× check failed')}</div><div class="term-divider"></div><div class="chat-prompt">${styled('prompt','❯')} <span class="caret" style="color:${shown('foreground')}"></span></div><div class="chat-prompt">${styled('prompt','%')} ${styled('muted','Agent working — you can type a follow-up')}</div>`;
  }
  function compare() {
    return `<div class="compare-heading">${styled('foreground','Normal and selected, side by side')}</div><div class="compare-row compare-column-head">${styled('muted','ELEMENT')}${styled('muted','NORMAL')}${styled('muted','SELECTED')}</div>`+roles.filter(r=>r.selectable).map(role=>`<div class="compare-row">${styled('muted',role.label)}${pick(role.id,role.label,'compare-pick',false)}${pick(role.id,role.label,'compare-pick',true)}</div>`).join('');
  }
  function contrast(a,b) {
    const luminance = color => rgb(color).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
    const x=luminance(a),y=luminance(b);return ((Math.max(x,y)+.05)/(Math.min(x,y)+.05)).toFixed(1);
  }
  function renderPreview() {
    const terminal=$('terminal'), role=byId[state.active];
    terminal.style.setProperty('--terminal-bg',shown('background'));terminal.style.setProperty('--terminal-fg',shown('foreground'));terminal.style.setProperty('--term-divider',shown('divider'));terminal.style.fontSize=state.fontSize+'px';
    $('terminal-content').innerHTML=state.view==='panel'?panel():state.view==='chat'?chat():compare();
    if (state.view !== 'compare') {
      const buttons = [...$('terminal-content').querySelectorAll('[data-selection]')];
      if (!buttons.some(button=>button.dataset.selection===state.selection)) {
        const next = buttons.find(button=>button.dataset.focus===state.active);
        state.selection = next?.dataset.selection || null;
        if (next) {next.setAttribute('aria-pressed','true');next.style.color=shown(state.active,'selected');next.style.fontWeight=state.bold?'700':'400';}
      }
    }
    $('terminal-hint').style.color=shown('hint');$('terminal-hint').textContent=state.view==='chat'?'↑ options   enter send   shift-enter newline   esc sessions':'↑↓ select   enter open   n new session   esc back';
    const normal=effective(role.id),selected=role.selectable?effective(role.id,'selected'):null;
    $('active-sample').innerHTML=`<span>${escapeText(role.label)}</span><div class="sample-chips"><span class="sample-chip"><i class="sample-dot" style="background:${normal}"></i>Normal <span class="sample-hex">${normal}</span></span>${selected?`<span class="sample-chip"><i class="sample-dot" style="background:${selected}"></i>Selected <span class="sample-hex">${selected}</span></span>`:''}</div>`;
    const background=shown(role.id==='userText'?'userBg':'background');
    $('contrast').textContent=role.id==='background'||role.id==='userBg'?'':`${contrast(shown(role.id),background)}:1 normal${selected?' · '+contrast(shown(role.id,'selected'),background)+':1 selected':''}`;
  }
  function renderPalette() {
    $('group-description').textContent=descriptions[state.group];
    $('palette').innerHTML=roles.filter(role=>role.group===state.group).map(role=>`<div class="color-row${state.active===role.id?' active':''}" data-row="${role.id}"><div class="role-name">${escapeText(role.label)}<small>${escapeText(role.note)}</small></div>${['normal','selected'].map(kind=>kind==='selected'&&!role.selectable?'<span class="no-selected">—</span>':`<div class="color-control"><input type="color" aria-label="${role.label} ${kind} color" data-role="${role.id}" data-kind="${kind}" value="${effective(role.id,kind)}"><input class="hex-input" aria-label="${role.label} ${kind} hex" data-role="${role.id}" data-kind="${kind}" value="${effective(role.id,kind)}" spellcheck="false" maxlength="7"></div>`).join('')}</div>`).join('');
  }
  function renderInspector() {
    const inspector=document.querySelector('.inspector'), bg=shown('background');
    const fg=Number(contrast('#d0d0d0',bg))>Number(contrast('#333333',bg))?'#d0d0d0':'#333333';
    inspector.style.colorScheme=state.theme;
    for (const [name,color] of Object.entries({bg,fg,muted:blend(fg,bg,.28),line:blend(fg,bg,.80),hover:blend(fg,bg,.94),active:blend(fg,bg,.88),accent:state.theme==='dark'?'#9ac1aa':'#246754'})) inspector.style.setProperty('--inspector-'+name,color);
    document.querySelectorAll('[data-row]').forEach(row=>{
      const id=row.dataset.row, canvas=['background','userBg','userText'].includes(id);
      row.querySelector('.role-name').style.color=canvas?fg:shown(id);
      row.querySelectorAll('.color-control').forEach(control=>{
        const kind=control.querySelector('input').dataset.kind;
        control.style.setProperty('--cell-bg',['userText','userBg'].includes(id)?shown('userBg'):bg);
        control.style.setProperty('--cell-fg',id==='background'?shown('foreground'):id==='userBg'?shown('userText'):shown(id,kind));
        control.style.setProperty('--cell-weight',kind==='selected'&&state.bold?'700':'400');
      });
    });
  }
  function refresh() {
    document.querySelectorAll('[data-theme]').forEach(el=>el.setAttribute('aria-pressed',el.dataset.theme===state.theme));
    document.querySelectorAll('[data-view]').forEach(el=>el.setAttribute('aria-pressed',el.dataset.view===state.view));
    document.querySelectorAll('[data-group]').forEach(el=>el.setAttribute('aria-selected',el.dataset.group===state.group));
    $('softness').value=state.palettes[state.theme].softness;$('softness-value').value=state.palettes[state.theme].softness+'%';$('quantize').checked=state.quantize;$('bold').checked=state.bold;$('font-size').value=state.fontSize;$('undo').disabled=!history.length;
    document.querySelectorAll('[data-row]').forEach(el=>el.classList.toggle('active',el.dataset.row===state.active));
    document.querySelectorAll('#palette input').forEach(el=>{if(el!==document.activeElement)el.value=effective(el.dataset.role,el.dataset.kind);});
    renderInspector();renderPreview();$('export-json').value=JSON.stringify(exported(),null,2);
    try {localStorage.setItem(storageKey,JSON.stringify(exported()));$('saved-label').textContent='Saved in this browser';} catch {$('saved-label').textContent='Export to keep your colors';}
  }
  function activate(id,selection) {
    state.active=id;
    if (selection) state.selection=selection;
    else if (byId[id].selectable) {
      const buttons=[...$('terminal-content').querySelectorAll('[data-selection]')].filter(button=>button.dataset.focus===id);
      if (!buttons.some(button=>button.dataset.selection===state.selection)) state.selection=buttons[0]?.dataset.selection || null;
    }
    const group=byId[id].group;if(state.group!==group){state.group=group;renderPalette();}refresh();
  }
  let toastTimer;
  function toast(message) {$('toast').textContent=message;$('toast').classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').classList.remove('visible'),3000);}
  async function copy(text,message) {
    try {await navigator.clipboard.writeText(text);toast(message);} catch {if(!$('export-dialog').open)$('export-dialog').showModal();$('export-json').value=text;$('export-json').focus();$('export-json').select();toast('Press ⌘C or Ctrl+C to copy.');}
  }
  document.querySelectorAll('[data-theme]').forEach(button=>button.addEventListener('click',()=>{state.theme=button.dataset.theme;renderPalette();refresh();}));
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>{state.view=button.dataset.view;refresh();}));
  document.querySelectorAll('[data-group]').forEach(button=>button.addEventListener('click',()=>{state.group=button.dataset.group;renderPalette();refresh();}));
  $('softness').addEventListener('input',event=>{remember();state.palettes[state.theme].softness=Number(event.target.value);refresh();});
  $('milder').addEventListener('click',()=>{remember();state.palettes[state.theme].softness=55;refresh();});
  $('palette').addEventListener('input',event=>{
    const input=event.target;if(!input.dataset.role)return;
    let color=input.value.trim();if(!color.startsWith('#'))color='#'+color;if(/^#[a-f\d]{3}$/i.test(color))color='#'+color.slice(1).split('').map(c=>c+c).join('');
    input.classList.toggle('invalid',!/^#[a-f\d]{6}$/i.test(color));if(!/^#[a-f\d]{6}$/i.test(color))return;
    remember();bake();state.palettes[state.theme].colors[input.dataset.role][input.dataset.kind]=color.toLowerCase();activate(input.dataset.role);
  });
  $('palette').addEventListener('focusin',event=>{if(event.target.dataset.role)activate(event.target.dataset.role);});
  $('terminal-content').addEventListener('click',event=>{const button=event.target.closest('[data-focus]');if(button){activate(button.dataset.focus,button.dataset.selection);$('terminal').focus({preventScroll:true});}});
  $('terminal').addEventListener('keydown',event=>{if(!['ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const selectable=roles.filter(r=>r.selectable),at=selectable.findIndex(r=>r.id===state.active),next=(at+(event.key==='ArrowDown'?1:-1)+selectable.length)%selectable.length;activate(selectable[next].id);$('terminal').focus();});
  for(const [id,key] of [['quantize','quantize'],['bold','bold'],['font-size','fontSize']])$(id).addEventListener('input',event=>{remember();state[key]=event.target.type==='checkbox'?event.target.checked:Number(event.target.value);refresh();});
  $('undo').addEventListener('click',()=>{if(history.length){state=history.pop();renderPalette();refresh();}});
  $('reset').addEventListener('click',()=>{remember();state.palettes[state.theme]=defaults(state.theme);renderPalette();refresh();toast('Current theme reset.');});
  document.querySelectorAll('[data-close]').forEach(button=>button.addEventListener('click',()=>$(button.dataset.close).close()));
  $('export-open').addEventListener('click',()=>{$('export-json').value=JSON.stringify(exported(),null,2);$('export-dialog').showModal();});
  $('copy-json').addEventListener('click',()=>copy(JSON.stringify(exported(),null,2),'Palette copied.'));
  $('download').addEventListener('click',()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(exported(),null,2)+'\n'],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='projector-console-colors.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('Palette downloaded.');});
  $('share').addEventListener('click',()=>{const url=new URL(location.href);url.hash=new URLSearchParams({palette:btoa(unescape(encodeURIComponent(JSON.stringify(exported()))))}).toString();copy(url.href,'Share link copied.');});
  $('import-open').addEventListener('click',()=>{$('import-error').textContent='';$('import-json').value='';$('import-dialog').showModal();$('import-json').focus();});
  $('apply-import').addEventListener('click',()=>{try {const imported=parse(JSON.parse($('import-json').value));remember();Object.assign(state,imported);renderPalette();refresh();$('import-dialog').close();toast('Palette imported.');} catch(error){$('import-error').textContent=error.message;}});
  renderPalette();refresh();
})();
