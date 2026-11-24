import { useEffect, useRef, useState } from "react";
import { Login, Toasts, TopBar } from "./components/Chrome";
import { JobsPanel } from "./components/JobsPanel";
import { NewPlan, filesFromDrop } from "./components/NewPlan";
import { PlanPage } from "./components/PlanPage";
import { Sidebar } from "./components/Sidebar";
import { AppProvider, useApp, useRoute } from "./store";

/** Drop files anywhere: they land in the New plan form. */
function DropZone() {
  const { setDropped } = useApp();
  const [show, setShow] = useState(false);
  const depth = useRef(0);
  useEffect(() => {
    const hasFiles = (e: DragEvent) => [...(e.dataTransfer?.types || [])].includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current++;
      setShow(true);
    };
    const over = (e: DragEvent) => hasFiles(e) && e.preventDefault();
    const leave = () => {
      if (--depth.current <= 0) {
        depth.current = 0;
        setShow(false);
      }
    };
    const drop = async (e: DragEvent) => {
      if (!hasFiles(e) || !e.dataTransfer) return;
      e.preventDefault();
      depth.current = 0;
      setShow(false);
      const files = await filesFromDrop(e.dataTransfer);
      if (files.length) {
        setDropped(files);
        if (!location.hash.startsWith("#/new")) location.hash = "#/new";
      }
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [setDropped]);
  return show ? <div className="dropzone"><div>Drop to add to a new plan</div></div> : null;
}

function Shell() {
  const { authed, jobs } = useApp();
  const route = useRoute();
  useEffect(() => {
    // Home: the newest plan if there is one, else the form.
    if (authed && route.page === "home") location.replace(jobs.length ? `#/p/${jobs[0].id}/plan` : "#/new");
  }, [authed, route.page, jobs]);

  if (authed === null) return null;
  if (!authed) {
    return (
      <>
        <header className="topbar"><div className="brand"><img src="/favicon.svg" alt="" /> fixplan</div></header>
        <Login />
      </>
    );
  }
  return (
    <>
      <TopBar />
      <div className="layout">
        <Sidebar route={route} />
        <main>{route.page === "plan" && route.jobId ? <PlanPage key={route.jobId} jobId={route.jobId} tab={route.tab} /> : <NewPlan />}</main>
      </div>
      <JobsPanel />
      <DropZone />
      <Toasts />
    </>
  );
}

export default function App() {
  return (
    <AppProvider>
      <Shell />
    </AppProvider>
  );
}
