// Geographic axes: +Y north, +Z Greenwich, +X 90 degrees east.
export const RAD = Math.PI / 180;
export const clamp = (n,lo,hi) => Math.max(lo,Math.min(hi,n));
export function geoVector(latitude, longitude) {
  const lat=latitude*RAD,lon=longitude*RAD;
  return [Math.cos(lat)*Math.sin(lon),Math.sin(lat),Math.cos(lat)*Math.cos(lon)];
}
export function rotateVector([x,y,z], yaw, pitch) {
  const xx=Math.cos(yaw)*x+Math.sin(yaw)*z;
  const zz=-Math.sin(yaw)*x+Math.cos(yaw)*z;
  return [xx,Math.cos(pitch)*y-Math.sin(pitch)*zz,Math.sin(pitch)*y+Math.cos(pitch)*zz];
}
export function projectGeo(latitude,longitude,yaw,pitch,width,height,zoom=1) {
  const [x,y,z]=rotateVector(geoVector(latitude,longitude),yaw,pitch);
  const radius=height*.43*zoom;
  const px=width/2+x*radius,py=height/2-y*radius;
  return {x:px,y:py,front:z>.04,visible:z>.04&&px>=0&&px<=width&&py>=0&&py<=height};
}
export function nearestAngle(target,current) {
  return current+Math.atan2(Math.sin(target-current),Math.cos(target-current));
}
export function focusAngles(latitude,longitude,currentYaw=0) {
  return {yaw:nearestAngle(-longitude*RAD,currentYaw),pitch:clamp(latitude,-85,85)*RAD};
}
export function zoomFromDelta(zoom,delta) { return clamp(zoom*Math.exp(-clamp(delta,-200,200)*.0015),.7,1.65); }
