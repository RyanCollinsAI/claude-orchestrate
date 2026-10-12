// Driver for test_board_calm.py: runs the Calm page's script against a tiny fake DOM and prints what
// the card host / progress area ended up holding. Not part of the page; node only.
//   node calm_js_probe.js <calm.js>
const fs = require('fs'), vm = require('vm');

const src = fs.readFileSync(process.argv[2], 'utf8').replace(/\}\)\(\);\s*$/,
  'globalThis.probe = {setState(s){ state = s; loaded = true; render(); }, tick, render};})();');

function boot(initial){
  const nodes = {};
  const mk = () => ({innerHTML: '', get firstChild(){ return this.innerHTML ? {} : null; }, textContent: '', hidden: false, className: '', setAttribute(){}, tabIndex: 0,
                     classList: {contains(){ return false; }, toggle(){}, add(){}}});
  const host = () => nodes['#cardhost'];
  const card = {draft: false, focused: false, field: {tagName: 'TEXTAREA'}};
  const ctx = {
    window: {__boardState: initial, matchMedia: () => ({matches: false}), scrollTo(){}},
    location: {search: ''}, URLSearchParams, Date,
    setTimeout(){}, clearTimeout(){}, setInterval(){},
    loadMode: 'fail', nextState: null,
  };
  ctx.document = {
    activeElement: null,
    documentElement: {setAttribute(){}},
    head: {appendChild(s){
      // a <script src=state.js> load: either succeeds with ctx.nextState or errors
      if (ctx.loadMode === 'ok'){ ctx.window.__boardState = ctx.nextState; s.onload(); } else s.onerror();
    }},
    createElement: () => ({remove(){}}),
    addEventListener(){},
    querySelector(sel){
      if (sel === '#card'){
        if (!/id="card"/.test(host().innerHTML)) return null;
        return {
          getAttribute: n => n === 'data-q' ? (/data-q="([^"]*)"/.exec(host().innerHTML) || [])[1] : null,
          contains: el => el === card.field,
          querySelectorAll: s => (s === 'textarea,input' && card.draft) ? [{value: 'half typed'}] : [],
          querySelector: () => null,
        };
      }
      return nodes[sel] || (nodes[sel] = mk());
    },
  };
  ctx.document.querySelector('#cardhost');
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return {ctx, nodes, card};
}

const st = (qs, n) => ({updated: n, questions: qs, sessions: [], done: [], links: []});
const q = {id: 'q1', title: 'Old question', options: [{value: 'yes', label: 'Yes'}], created: '2026-10-09T10:00:00Z'};
const out = {};

// load failure, then a later success
let r = boot(undefined);
out.failShows = r.nodes['#cardhost'].innerHTML;
r.ctx.loadMode = 'fail';
r.ctx.probe.tick().then(() => {
  out.failStaysAfterBadTick = r.nodes['#cardhost'].innerHTML;
  r.ctx.loadMode = 'ok';
  r.ctx.nextState = st([q], 5);
  return r.ctx.probe.tick();
}).then(() => {
  out.recovered = r.nodes['#cardhost'].innerHTML;

  // same id, changed title and options
  r = boot(st([q], 1));
  r.ctx.probe.setState(st([{...q, title: 'Replacement', options: [{value: 'no', label: 'No'}]}], 2));
  out.changed = r.nodes['#cardhost'].innerHTML;

  // typed draft + a new urgent question
  const free = {...q, options: []};
  const urgent = {...q, id: 'q2', title: 'New urgent', due: 'today', options: []};
  r = boot(st([free], 1));
  r.card.draft = true;
  r.ctx.probe.setState(st([free, urgent], 2));
  out.draftHost = r.nodes['#cardhost'].innerHTML;
  out.draftProg = r.nodes['#prog'].innerHTML;

  // focus alone (empty field) counts too
  r = boot(st([free], 1));
  r.ctx.document.activeElement = r.card.field;
  r.ctx.probe.setState(st([free, urgent], 2));
  out.focusHost = r.nodes['#cardhost'].innerHTML;

  // nothing typed: the urgent one takes the stage, no pill
  r = boot(st([free], 1));
  r.ctx.probe.setState(st([free, urgent], 2));
  out.cleanHost = r.nodes['#cardhost'].innerHTML;
  out.cleanProg = r.nodes['#prog'].innerHTML;

  // typed draft but its question was answered/removed: replaced, not kept
  r = boot(st([free], 1));
  r.card.draft = true;
  r.ctx.probe.setState(st([urgent], 2));
  out.goneHost = r.nodes['#cardhost'].innerHTML;

  console.log(JSON.stringify(out));
}).catch(e => { console.error(e); process.exit(1); });
