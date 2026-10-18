'use strict';
// Aucune ressource réseau externe, aucun suivi des utilisateurs.
document.addEventListener('DOMContentLoaded', () => {
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
