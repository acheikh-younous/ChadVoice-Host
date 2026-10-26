'use strict';
// Aucune ressource réseau externe, aucun suivi des utilisateurs.
document.addEventListener('DOMContentLoaded', () => {
  // Navigation mobile accessible : bouton, clic hors menu, liens et touche Échap.
  const menuToggle=document.querySelector('[data-menu-toggle]');
  const menuClose=document.querySelector('[data-menu-close]');
  const sidebar=document.getElementById('primary-sidebar');
  const setMenuOpen=(isOpen)=>{
    document.body.classList.toggle('menu-open',isOpen);
    menuToggle?.setAttribute('aria-expanded',String(isOpen));
    menuToggle?.setAttribute('aria-label',isOpen?'Fermer le menu':'Ouvrir le menu');
  };
  menuToggle?.addEventListener('click',()=>setMenuOpen(!document.body.classList.contains('menu-open')));
  menuClose?.addEventListener('click',()=>setMenuOpen(false));
  sidebar?.querySelectorAll('nav a').forEach(link=>link.addEventListener('click',()=>setMenuOpen(false)));
  document.addEventListener('keydown',event=>{if(event.key==='Escape')setMenuOpen(false);});
  window.matchMedia('(min-width: 801px)').addEventListener('change',event=>{if(event.matches)setMenuOpen(false);});
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
