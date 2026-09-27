import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
const { chromium }=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const root=path.resolve(import.meta.dirname,"../..");
// The design package is local material, kept out of git (see .gitignore).
// The folder name is spelled exactly, so this also works where paths are case-sensitive.
const pkg=path.join(root,"docs/design/design/Zenoeats-Design-Package");
const frames=JSON.parse(await fs.readFile(path.join(pkg,"frame-manifest.json"),"utf8"));
const out=path.join(root,"artifacts/redesign/references");await fs.mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true});
const rows=[];let cursor=0;
await Promise.all(Array.from({length:3},async()=>{
 const page=await browser.newPage({viewport:{width:1600,height:1000}});
 page.setDefaultNavigationTimeout(60000);
 while(cursor<frames.length) {
  const f=frames[cursor++];
  try {
   await page.goto("http://127.0.0.1:3200/"+f.file,{waitUntil:"load"});
   const frame=page.frames()[1];
   const text=await frame.locator("body").innerText();
   const controls=await frame.locator("button,input,select,textarea,a").count();
   if(f.state==="default") await page.locator("iframe").screenshot({path:path.join(out,f.id+".png")});
   rows.push({id:f.id,screen:f.screen,state:f.state,width:f.width,status:"rendered",controls,text});
  }catch(error){rows.push({id:f.id,status:"failed",error:error.message});}
  if(rows.length%100===0) console.log("Rendered "+rows.length+" / "+frames.length);
 }
 await page.close();
}));
await fs.writeFile(path.join(out,"render-results.json"),JSON.stringify(rows,null,2));
await browser.close();console.log(JSON.stringify({total:rows.length,failed:rows.filter(r=>r.status==="failed").length}));
process.exitCode=rows.some(r=>r.status==="failed")?1:0;
