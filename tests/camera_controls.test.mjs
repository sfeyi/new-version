import test from "node:test";
import assert from "node:assert/strict";
import {filterSettings, filterPixels, filterCSS} from "../static/filters.js";
import {FocusController, cropRect, cameraPoint} from "../static/focus.js";

function camera({ignore=false, reject=false, wait=null} = {}) {
  let settings={focusMode:"continuous", focusDistance:2};
  const calls=[];
  return {calls, readyState:"live",
    getCapabilities:() => ({focusMode:["continuous","single-shot","manual"], focusDistance:{min:0,max:10,step:.5}}),
    getSettings:() => settings,
    getConstraints:() => ({width:{ideal:1920}, facingMode:{ideal:"environment"}, advanced:[{torch:true,focusMode:"manual",focusDistance:2}]}),
    async applyConstraints(value) {
      calls.push(value);
      if (wait) await wait;
      if (reject) throw new Error("camera rejected request");
      if (!ignore) settings={...settings,...value.advanced.at(-1)};
    },
  };
}

test("original and zero-strength effects preserve all bytes, including alpha", () => {
  const p=new Uint8ClampedArray([250,20,60,128,0,255,120,255]);
  assert.deepEqual(filterPixels(p.slice(),filterSettings()),p);
  assert.deepEqual(filterPixels(p.slice(),filterSettings("warm",0)),p);
  assert.equal(filterCSS(filterSettings()),"none");
  assert.throws(() => filterSettings("missing"),RangeError);
});

test("monochrome affects RGB but leaves alpha; partial strength retains color", () => {
  const p=new Uint8ClampedArray([220,80,20,153]);
  const mono=filterPixels(p.slice(),filterSettings("mono"));
  assert.equal(mono[0],mono[1]); assert.equal(mono[1],mono[2]); assert.equal(mono[3],153);
  const partial=filterPixels(p.slice(),filterSettings("mono",.5));
  assert.ok(partial[0]>partial[1] && partial[1]>partial[2]);
});

test("focus points account for portrait crop, digital zoom and source orientation", () => {
  assert.deepEqual(cropRect(1920,1080,.75),{x:555,y:0,width:810,height:1080});
  assert.deepEqual(cameraPoint([.5,.5],1920,1080,.75,1.6),{x:.5,y:.5});
  const p=cameraPoint([0,0],1920,1080,.75,1.6);
  assert.ok(Math.abs(p.x-.3681640625)<1e-9); assert.equal(p.y,.1875);
  assert.deepEqual(cameraPoint([0,0],1080,1920,1,1),{x:0,y:.21875});
  assert.throws(() => cameraPoint([NaN,.2],100,100,1));
});

test("global browser support alone does not enable camera focus", async () => {
  const focus=new FocusController().bind({getCapabilities:()=>({}), applyConstraints(){}},{pointsOfInterest:true});
  assert.equal(focus.canPoint,false); assert.equal(focus.distance,null);
  assert.equal((await focus.auto()).status,"unavailable");
  focus.bind({getCapabilities(){throw new Error("denied");}});
  assert.equal(focus.autoMode,null);
});

test("point autofocus preserves non-focus constraints and replaces old manual focus", async () => {
  const track=camera(), focus=new FocusController().bind(track,{pointsOfInterest:true});
  assert.equal((await focus.point({x:.3,y:.6})).status,"requested");
  assert.deepEqual(track.calls[0],{width:{ideal:1920},facingMode:{ideal:"environment"},
    advanced:[{torch:true},{focusMode:"single-shot",pointsOfInterest:[{x:.3,y:.6}]}]});
  assert.equal((await focus.auto()).status,"requested");
  assert.equal(track.calls[1].advanced.at(-1).focusMode,"continuous");
});

test("manual focus respects device range and step", async () => {
  const track=camera(), focus=new FocusController().bind(track);
  assert.equal((await focus.manual(.26)).status,"requested");
  assert.equal(track.calls[0].advanced.at(-1).focusDistance,2.5);
  await focus.manual(2);
  assert.equal(track.calls[1].advanced.at(-1).focusDistance,10);
});

test("ignored and rejected controls are reported, never claimed as focus success", async () => {
  const ignored=new FocusController().bind(camera({ignore:true}),{pointsOfInterest:true});
  assert.equal((await ignored.point({x:.2,y:.7})).status,"unconfirmed");
  const failed=new FocusController().bind(camera({reject:true}));
  assert.equal((await failed.auto()).status,"failed"); assert.equal(failed.busy,false);
});

test("camera switch invalidates pending results; overlapping requests cannot race", async () => {
  let finish;
  const wait=new Promise(resolve=>{finish=resolve;});
  const focus=new FocusController().bind(camera({wait}));
  const pending=focus.auto();
  assert.equal((await focus.manual(.5)).status,"busy");
  focus.bind(camera());
  finish();
  assert.equal((await pending).status,"stale");
  assert.equal(focus.busy,false);
  assert.equal((await focus.auto()).status,"requested");
});

test("timed-out hardware requests require rebinding instead of racing new commands", async () => {
  const focus=new FocusController(5).bind(camera({wait:new Promise(()=>{})}));
  assert.equal((await focus.auto()).status,"timeout");
  assert.equal(focus.busy,false); assert.equal(focus.blocked,true);
  assert.equal((await focus.manual(.5)).status,"unavailable");
  focus.bind(camera());
  assert.equal((await focus.auto()).status,"requested");
});
