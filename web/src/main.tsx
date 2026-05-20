/* @refresh reload */
import { render } from "solid-js/web";
import { Router } from "@solidjs/router";
import "./index.css";
import { App } from "./app";
import { ToastLayer } from "./shared/toast";
import { TokenGate } from "./shared/token_gate";

render(() => (
  <>
    <Router>
      <App />
    </Router>
    <ToastLayer />
    <TokenGate />
  </>
), document.getElementById("root")!);
