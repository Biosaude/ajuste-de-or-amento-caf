import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';
type Base = {name:string; updated_at:string; count:number};
type Analysis = {budget_number:string; patient:string; item_count:number; total:string};
type Result = Analysis & {token:string; missing_count:number; preview:{original_order:number;new_order:number;code:string;description:string}[]};

async function json(res:Response) { const body=await res.json(); if(!res.ok) throw new Error(typeof body.detail==='string'?body.detail:(body.detail?.message||'Falha no processamento.')); return body; }
function App(){
 const [base,setBase]=useState<Base|null>(null),[pdf,setPdf]=useState<File|null>(null),[analysis,setAnalysis]=useState<Analysis|null>(null),[result,setResult]=useState<Result|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{fetch(`${API}/api/base`).then(json).then(setBase).catch(()=>{})},[]);
 async function uploadBase(file:File){setBusy(true);setError('');const f=new FormData();f.append('file',file);try{setBase(await json(await fetch(`${API}/api/base`,{method:'POST',body:f})));}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 async function choosePdf(file:File){setPdf(file);setResult(null);setBusy(true);setError('');const f=new FormData();f.append('file',file);try{setAnalysis(await json(await fetch(`${API}/api/pdf/analyze`,{method:'POST',body:f})));}catch(e){setAnalysis(null);setError((e as Error).message)}finally{setBusy(false)}}
 async function process(){if(!pdf)return;setBusy(true);setError('');const f=new FormData();f.append('file',pdf);try{setResult(await json(await fetch(`${API}/api/process`,{method:'POST',body:f})));}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <main><header><div className="mark">VO</div><div><h1>Ordenador de Orçamentos</h1><p>Organize os itens sem alterar o padrão do documento original</p></div><span className="local">● Processamento local seguro</span></header>
 {error&&<div className="error"><b>Não foi possível concluir</b><span>{error}</span></div>}
 <section><div className="step">1</div><div className="content"><h2>Planilha Base</h2><p>Selecione a referência que define a sequência dos materiais.</p>{base&&<div className="success"><b>✓ Planilha base atual</b><span>{base.name} · {base.count} códigos · atualizada em {new Date(base.updated_at).toLocaleString('pt-BR')}</span></div>}<label className="secondary">{base?'Atualizar Planilha Base':'Selecionar Excel'}<input type="file" accept=".xlsx,.xls" disabled={busy} onChange={e=>e.target.files?.[0]&&uploadBase(e.target.files[0])}/></label></div></section>
 <section><div className="step">2</div><div className="content"><h2>Orçamento</h2><p>Envie o PDF exportado pelo VIMAN.</p><label className="drop"><b>{pdf?pdf.name:'Selecionar PDF'}</b><span>PDF com camada de texto pesquisável</span><input type="file" accept="application/pdf" disabled={busy} onChange={e=>e.target.files?.[0]&&choosePdf(e.target.files[0])}/></label>{analysis&&<div className="facts"><span><small>ORÇAMENTO</small>{analysis.budget_number}</span><span><small>PACIENTE</small>{analysis.patient}</span><span><small>ITENS</small>{analysis.item_count}</span></div>}</div></section>
 <section><div className="step">3</div><div className="content"><h2>Gerar orçamento ordenado</h2><p>O sistema reorganizará somente as linhas da tabela e validará a integridade.</p><button disabled={!base||!analysis||busy} onClick={process}>{busy?'PROCESSANDO…':'GERAR ORÇAMENTO ORDENADO'}</button>{result&&<><div className="success"><b>✓ Ordenação concluída &nbsp; ✓ Integridade validada</b><span>{result.item_count} itens · Total preservado: {result.total}</span></div>{result.missing_count>0&&<div className="warning">{result.missing_count} item(ns) do orçamento não foram encontrados na planilha base e foram mantidos ao final do documento.</div>}<a className="download" href={`${API}/api/download/${result.token}`}>BAIXAR PDF ORDENADO</a></>}</div></section>
 {result&&<section className="preview"><div className="content"><h2>Pré-visualização da ordenação</h2><table><thead><tr><th>ORDEM ORIGINAL</th><th>ORDEM NOVA</th><th>CÓDIGO</th><th>PRODUTO</th></tr></thead><tbody>{result.preview.map(x=><tr key={`${x.original_order}-${x.code}`}><td>{x.original_order}</td><td>→ {x.new_order}</td><td><b>{x.code}</b></td><td>{x.description}</td></tr>)}</tbody></table></div></section>}
 <footer>Os arquivos são processados no servidor privado e não são enviados a serviços externos.</footer></main>
}
createRoot(document.getElementById('root')!).render(<App/>);

