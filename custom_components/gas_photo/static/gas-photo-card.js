class GasPhotoCard extends HTMLElement {
  setConfig(config) { this.config = config; this.offset=0; this.render(); }
  set hass(hass) { this._hass=hass; if(!this.loaded && !this.busy) this.load(); }
  getCardSize() { return 5; }
  async load() {
    if(!this._hass || this.busy) return;
    this.busy=true; this.error='';
    try { const result=await this._hass.callWS({type:'gas_photo/get_readings',offset:this.offset,limit:50}); this.rows=result.readings; this.loaded=true; }
    catch(e) { this.error=String(e.message||e); }
    finally { this.busy=false; this.render(); }
  }
  render() {
    this.replaceChildren();
    const card=document.createElement('ha-card'); card.header=this.config?.title||'Pontos gázóra-leolvasások';
    const body=document.createElement('div'); body.style.cssText='padding:16px;overflow:auto';
    const note=document.createElement('p'); note.textContent='Eredeti fotóidő, UTC-eltolással. A fogyasztás a két fotó között mért különbség; nem napi profil.'; body.append(note);
    if(this.error) {const p=document.createElement('p');p.textContent=this.error;body.append(p);}
    const table=document.createElement('table');table.style.cssText='width:100%;text-align:left';
    const header=document.createElement('tr'); for(const label of ['Fotóidő','Mérőállás (m³)','Változás (m³)']){const th=document.createElement('th');th.textContent=label;header.append(th);} table.append(header);
    (this.rows||[]).forEach((r,i)=>{const tr=document.createElement('tr');const delta=i?((Math.round(Number(r.value)*1000)-Math.round(Number(this.rows[i-1].value)*1000))/1000).toFixed(3):'—';for(const value of [r.captured_at,r.value,delta]){const td=document.createElement('td');td.textContent=value;td.style.padding='8px 4px';tr.append(td);}table.append(tr);});body.append(table);
    for(const [label,change,disabled] of [['Előző',-50,this.offset===0],['Frissítés',0,false],['Következő',50,(this.rows||[]).length<50]]){const button=document.createElement('button');button.textContent=label;button.disabled=disabled||this.busy;button.onclick=()=>{this.offset=Math.max(0,this.offset+change);this.load();};body.append(button);}
    card.append(body);this.append(card);
  }
}
customElements.define('gas-photo-card',GasPhotoCard);
window.customCards=window.customCards||[];
window.customCards.push({type:'gas-photo-card',name:'Gas Photo exact history',description:'Original photo timestamps and reviewed gas meter readings'});
