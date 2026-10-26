'use strict';
// Aucune ressource réseau externe, aucun suivi des utilisateurs.
document.addEventListener('DOMContentLoaded', () => {
  // Navigation mobile accessible : bouton, clic hors menu, liens et touche Échap.
  const menuToggle=document.querySelector('[data-menu-toggle]');
  const sidebar=document.getElementById('primary-sidebar');
  const main=document.querySelector('.main');
  const mobile=window.matchMedia('(max-width: 800px)');
  const focusable='a[href],button,input,select,textarea,[tabindex="0"]';
  const setMenuOpen=(isOpen,restoreFocus=true)=>{
    isOpen=Boolean(isOpen && mobile.matches && sidebar);
    document.body.classList.toggle('menu-open',isOpen);
    menuToggle?.setAttribute('aria-expanded',String(isOpen));
    menuToggle?.setAttribute('aria-label',isOpen?'Fermer le menu':'Ouvrir le menu');
    if(sidebar)sidebar.inert=mobile.matches && !isOpen;
    if(main)main.inert=isOpen;
    if(isOpen)sidebar.querySelector(focusable)?.focus();
    else if(restoreFocus && mobile.matches)menuToggle?.focus();
  };
  setMenuOpen(false,false);
  menuToggle?.addEventListener('click',()=>setMenuOpen(!document.body.classList.contains('menu-open')));
  document.querySelectorAll('[data-menu-close]').forEach(close=>close.addEventListener('click',()=>setMenuOpen(false)));
  sidebar?.querySelectorAll('nav a').forEach(link=>link.addEventListener('click',()=>setMenuOpen(false)));
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && document.body.classList.contains('menu-open'))setMenuOpen(false);
    if(event.key==='Tab' && document.body.classList.contains('menu-open')){
      const items=[...sidebar.querySelectorAll(focusable)].filter(el=>!el.disabled && el.getClientRects().length);
      const first=items[0],last=items[items.length-1];
      if(event.shiftKey && document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();}
    }
  });
  mobile.addEventListener('change',()=>setMenuOpen(false,false));
  document.querySelectorAll('form[data-confirm]').forEach(f => f.addEventListener('submit', e => {
    if(!confirm(f.dataset.confirm)) e.preventDefault();
  }));
  const form = document.getElementById('contribute-form');
  if(form){
    form.addEventListener('submit', ev => {
      const action=ev.submitter?.value;
      if(action!=='submit') return;
      const latin=form.querySelector('[name=latin]').value.trim();
      const french=form.querySelector('[name=french]').value.trim();
      const flag=form.querySelector('[name=inaudible]').checked;
      if(!flag && (!latin||!french)){
        ev.preventDefault(); alert('Les deux textes sont obligatoires avant la soumission, sauf pour un audio inaudible.');
      }
    });
  }
});
