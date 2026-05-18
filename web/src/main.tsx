/* @refresh reload */
import { render } from "solid-js/web";
import { Router } from "@solidjs/router";
import "./index.css";
import { App } from "./app";
import { ToastLayer } from "./shared/toast";

render(() => (
  <>
    <Router>
      <App />
    </Router>
    <ToastLayer />
  </>
), document.getElementById("root")!);
