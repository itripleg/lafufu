import { Route } from "@solidjs/router";
import { lazy } from "solid-js";
import { Landing } from "./landing";

const Face  = lazy(() => import("./face/face"));
const Admin = lazy(() => import("./admin/admin"));
const Pet   = lazy(() => import("./pet/pet"));

export function App() {
  return (
    <>
      <Route path="/" component={Landing} />
      <Route path="/face"  component={Face}  />
      <Route path="/pet"   component={Pet}   />
      <Route path="/admin" component={Admin} />
    </>
  );
}
