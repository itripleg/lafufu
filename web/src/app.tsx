import { Route } from "@solidjs/router";
import { lazy } from "solid-js";

const Face = lazy(() => import("./face/face"));
const Admin = lazy(() => import("./admin/admin"));

export function App() {
  return (
    <>
      <Route path="/" component={() => <div class="p-4">Lafufu — pick <a href="/face" class="underline">/face</a> or <a href="/admin" class="underline">/admin</a></div>} />
      <Route path="/face" component={Face} />
      <Route path="/admin" component={Admin} />
    </>
  );
}
