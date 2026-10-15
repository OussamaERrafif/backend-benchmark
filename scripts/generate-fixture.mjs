import fs from 'node:fs';
fs.mkdirSync('fixtures', {recursive:true});
const items=Array.from({length:800},(_,i)=>({id:i+1,name:`Item ${i+1}`,description:'Deterministic benchmark payload shared by all eight backend frameworks.',active:i%2===0,score:i%100}));
fs.writeFileSync('fixtures/large.json',JSON.stringify({items})+'\n');
for(const name of ['plaintext','json','json-large','compute','db-read','db-list','db-create','db-update','mixed']) {
  fs.writeFileSync(`load-tests/${name}.js`, `export {options,handleSummary} from './common.js';\nimport {run} from './common.js';\nexport default function(){run('${name}');}\n`);
}
let a=0;for(let i=1;i<=100000;i++)a=(a+((i*i)%97))%1000000007;
console.log({fixtureBytes:fs.statSync('fixtures/large.json').size,compute:a});
