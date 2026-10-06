export const themeStorageKey='sijav-todo-dashboard-theme';
const validTheme=value=>value==='dark'||value==='light';
export const resolveTheme=(saved,systemDark)=>validTheme(saved)?saved:systemDark?'dark':'light';
export const toggleTheme=current=>resolveTheme(current,false)==='dark'?'light':'dark';
export function readThemePreference(storage,key=themeStorageKey){
  try{const value=storage?.getItem(key);return validTheme(value)?value:null;}catch{return null;}
}
export function storeThemePreference(storage,theme,key=themeStorageKey){
  if(!validTheme(theme)||!storage)return false;
  try{storage.setItem(key,theme);return true;}catch{return false;}
}

// Fullscreen and wake-lock requests can settle after the user exits. Session
// tokens prevent a late promise from reviving the display or keeping a lock.
export function createDisplayController({element,document,wakeLock,onChange=()=>{},onStatus=()=>{}}){
  let active=false,session=0,sentinel=null,wakeRequest=null,nativeOwned=false;
  let status={fullscreen:'window',wake:'unavailable'};
  const report=patch=>{status={...status,...patch};onStatus({...status});};
  const release=lock=>{try{return Promise.resolve(lock?.release()).catch(()=>{});}catch{return Promise.resolve();}};
  const exitOwnedFullscreen=()=>{
    if(document.fullscreenElement!==element)return Promise.resolve();
    // A document whose fullscreen element is ours implements the Fullscreen API, exitFullscreen included;
    // any throw, a missing method's TypeError too, is caught here.
    try{return Promise.resolve(document.exitFullscreen()).catch(()=>{});}catch{return Promise.resolve();}
  };
  const acquireWake=()=>{
    if(!active||document.visibilityState==='hidden'||sentinel||wakeRequest)return;
    if(typeof wakeLock?.request!=='function'){report({wake:'unavailable'});return;}
    const token=session;report({wake:'requesting'});
    let requested;
    try{requested=wakeLock.request('screen');}catch{report({wake:'unavailable'});return;}
    const pending=Promise.resolve(requested).then(lock=>{
      if(!active||token!==session){return release(lock);}
      sentinel=lock;report({wake:'active'});
      lock.addEventListener?.('release',()=>{
        if(sentinel===lock){sentinel=null;if(active)report({wake:'released'});}
      },{once:true});
    },()=>{if(active&&token===session)report({wake:'unavailable'});}).finally(()=>{if(wakeRequest===pending)wakeRequest=null;});
    wakeRequest=pending;
  };
  const requestFullscreen=()=>{
    if(!active)return Promise.resolve();
    const token=session;
    let fullscreen;
    if(typeof element.requestFullscreen==='function'&&document.fullscreenEnabled!==false){
      report({fullscreen:'requesting'});
      // Invoke immediately in the click handler to retain browser user activation.
      try{fullscreen=element.requestFullscreen();}catch{report({fullscreen:'window'});}
    }
    return Promise.resolve(fullscreen).then(()=>{
      if(!active)return exitOwnedFullscreen();
      if(token!==session)return;
      nativeOwned=document.fullscreenElement===element;
      report({fullscreen:nativeOwned?'full':'window'});
    },()=>{if(active&&token===session)report({fullscreen:'window'});});
  };
  const enter=()=>{
    if(active)return Promise.resolve();
    active=true;++session;nativeOwned=false;
    status={fullscreen:'window',wake:'unavailable'};onChange(true);report({});
    const requested=requestFullscreen();acquireWake();return requested;
  };
  const exit=()=>{
    if(!active)return Promise.resolve();
    active=false;++session;nativeOwned=false;
    const lock=sentinel;sentinel=null;wakeRequest=null;
    onChange(false);report({fullscreen:'window',wake:'unavailable'});
    return Promise.allSettled([release(lock),exitOwnedFullscreen()]);
  };
  const fullscreenChanged=()=>{
    if(!active)return;
    if(document.fullscreenElement===element){nativeOwned=true;report({fullscreen:'full'});}
    else if(nativeOwned)exit();
  };
  const visibilityChanged=()=>{if(document.visibilityState!=='hidden')acquireWake();};
  return {enter,exit,requestFullscreen,fullscreenChanged,visibilityChanged,dispose:exit,get active(){return active;}};
}
