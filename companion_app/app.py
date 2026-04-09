from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk

from . import APP_VERSION, paths, store
from .pipeline import ensure_project_camera_manifests, ingest_media_sources, list_project_images
from .ply import PreviewPoint, read_preview_points


class PreviewCanvas(tk.Canvas):
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg="#f3eee6", highlightthickness=0)
        self.points: list[PreviewPoint] = []
        self.angle_x = -0.35
        self.angle_y = 0.75
        self.zoom = 220.0
        self.last_mouse = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_points(self, points: list[PreviewPoint]) -> None:
        self.points = points
        self.redraw()

    def _on_press(self, event: tk.Event) -> None:
        self.last_mouse = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        if not self.last_mouse:
            return
        dx = event.x - self.last_mouse[0]
        dy = event.y - self.last_mouse[1]
        self.last_mouse = (event.x, event.y)
        self.angle_y += dx * 0.01
        self.angle_x += dy * 0.01
        self.redraw()

    def _on_wheel(self, event: tk.Event) -> None:
        self.zoom = max(40.0, min(640.0, self.zoom + (event.delta / 120.0) * 18.0))
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if not self.points:
            self.create_text(
                self.winfo_width() / 2,
                self.winfo_height() / 2,
                text="Run a job to preview the generated splat.",
                fill="#6c6258",
                font=("Georgia", 12),
            )
            return

        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        cos_x, sin_x = math.cos(self.angle_x), math.sin(self.angle_x)
        cos_y, sin_y = math.cos(self.angle_y), math.sin(self.angle_y)
        rendered = []
        for point in self.points:
            x, y, z = point.x, point.y, point.z
            xz_x = (x * cos_y) + (z * sin_y)
            xz_z = (-x * sin_y) + (z * cos_y)
            yz_y = (y * cos_x) - (xz_z * sin_x)
            yz_z = (y * sin_x) + (xz_z * cos_x)
            depth = yz_z + 3.5
            if depth <= 0.1:
                continue
            scale = self.zoom / depth
            screen_x = (width * 0.5) + (xz_x * scale)
            screen_y = (height * 0.5) - (yz_y * scale)
            radius = max(1.0, min(8.0, point.scale * 0.08 * scale))
            rendered.append((depth, screen_x, screen_y, radius, point))

        for _depth, screen_x, screen_y, radius, point in sorted(rendered, key=lambda item: item[0], reverse=True):
            color = "#%02x%02x%02x" % (
                int(point.r * 255),
                int(point.g * 255),
                int(point.b * 255),
            )
            self.create_oval(
                screen_x - radius,
                screen_y - radius,
                screen_x + radius,
                screen_y + radius,
                fill=color,
                outline="",
            )


class CompanionApp:
    POLL_MS = 1200

    def __init__(self, root: tk.Tk, plugin_root: str | None = None) -> None:
        self.root = root
        self.plugin_root = plugin_root
        self.selected_project_id: str | None = None
        self.preview_path: str | None = None

        paths.ensure_runtime_dirs()
        store.init_db()
        self._configure_root()
        self._build_ui()
        self.refresh_projects()
        self.root.after(self.POLL_MS, self._poll)

    def _sample_dataset_dir(self) -> Path | None:
        root = Path(__file__).resolve().parents[1]
        sample_dir = root / "sample_datasets" / "nerf_synthetic_lego_12" / "images"
        return sample_dir if sample_dir.exists() else None

    def _configure_root(self) -> None:
        self.root.title("Gaussian Points Companion")
        self.root.geometry("1360x860")
        self.root.minsize(1180, 760)
        self.root.configure(bg="#ece3d4")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("App.TFrame", background="#ece3d4")
        style.configure("Panel.TFrame", background="#fbf7f1")
        style.configure("Panel.TLabel", background="#fbf7f1", foreground="#241d18")
        style.configure("Hero.TLabel", background="#fbf7f1", foreground="#241d18", font=("Georgia", 24, "bold"))
        style.configure("Sub.TLabel", background="#fbf7f1", foreground="#6f6458", font=("Segoe UI", 10))
        style.configure("Body.TLabel", background="#fbf7f1", foreground="#3d332b", font=("Segoe UI", 10))
        style.configure("Accent.TButton", background="#b75d39", foreground="#ffffff", borderwidth=0, padding=10)
        style.map("Accent.TButton", background=[("active", "#9f4d2b")])
        style.configure("Quiet.TButton", background="#efe6d9", foreground="#2f251f", borderwidth=0, padding=10)
        style.configure("Treeview", background="#fffdf9", fieldbackground="#fffdf9", rowheight=28)
        style.configure("Treeview.Heading", background="#efe4d5", foreground="#30261f")

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame", padding=18)
        shell.pack(fill="both", expand=True)

        sidebar = ttk.Frame(shell, style="Panel.TFrame", padding=14)
        sidebar.pack(side="left", fill="y")

        ttk.Label(sidebar, text="Projects", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text="Create a project from photos, or start with the bundled Lego sample set.",
            style="Sub.TLabel",
            wraplength=260,
        ).pack(anchor="w", pady=(6, 14))

        self.project_tree = ttk.Treeview(sidebar, columns=("status",), show="tree headings", height=24)
        self.project_tree.heading("#0", text="Project")
        self.project_tree.heading("status", text="Status")
        self.project_tree.column("#0", width=210)
        self.project_tree.column("status", width=94, anchor="center")
        self.project_tree.pack(fill="y", expand=True)
        self.project_tree.bind("<<TreeviewSelect>>", lambda _event: self._on_project_selected())

        sidebar_actions = ttk.Frame(sidebar, style="Panel.TFrame")
        sidebar_actions.pack(fill="x", pady=(12, 0))
        ttk.Button(
            sidebar_actions,
            text="New Project From Photos",
            style="Accent.TButton",
            command=self.create_project,
        ).pack(fill="x")
        ttk.Button(
            sidebar_actions,
            text="Add Photos To Project",
            style="Quiet.TButton",
            command=self.add_photos_to_project,
        ).pack(fill="x", pady=(8, 0))
        if self._sample_dataset_dir():
            ttk.Button(
                sidebar_actions,
                text="Create Sample Project",
                style="Quiet.TButton",
                command=self.create_sample_project,
            ).pack(fill="x", pady=(8, 0))
        ttk.Button(sidebar_actions, text="Reveal Data Folder", style="Quiet.TButton", command=self.open_data_folder).pack(fill="x", pady=(8, 0))

        content = ttk.Frame(shell, style="App.TFrame")
        content.pack(side="left", fill="both", expand=True, padx=(18, 0))

        hero = ttk.Frame(content, style="Panel.TFrame", padding=18)
        hero.pack(fill="x")
        ttk.Label(hero, text="Gaussian Splat Studio", style="Hero.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text=(
                "Separate desktop workflow for image-set reconstruction, preview, and SketchUp-ready export.\n"
                "Quick start: 1) New Project From Photos or Create Sample Project  2) select a project  3) press Start.\n"
                "The current backend expects a roughly centered turntable-style capture on a clean background."
            ),
            style="Sub.TLabel",
            wraplength=860,
        ).pack(anchor="w", pady=(6, 0))

        controls = ttk.Frame(content, style="App.TFrame")
        controls.pack(fill="x", pady=(14, 0))
        self.project_title = ttk.Label(controls, text="No project selected", style="Hero.TLabel")
        self.project_title.pack(anchor="w")
        self.project_meta = ttk.Label(controls, text="Choose or create a project to start.", style="Sub.TLabel")
        self.project_meta.pack(anchor="w", pady=(4, 10))

        button_bar = ttk.Frame(controls, style="App.TFrame")
        button_bar.pack(fill="x")
        ttk.Button(button_bar, text="Start", style="Accent.TButton", command=self.start_job).pack(side="left")
        ttk.Button(button_bar, text="Continue", style="Quiet.TButton", command=self.continue_job).pack(side="left", padx=(8, 0))
        ttk.Button(button_bar, text="Restart", style="Quiet.TButton", command=self.restart_job).pack(side="left", padx=(8, 0))
        ttk.Button(button_bar, text="Stop", style="Quiet.TButton", command=self.stop_job).pack(side="left", padx=(8, 0))
        ttk.Button(button_bar, text="Open Latest Export", style="Quiet.TButton", command=self.open_latest_export).pack(side="left", padx=(16, 0))

        cards = ttk.Frame(content, style="App.TFrame")
        cards.pack(fill="x", pady=(14, 0))
        self.status_card = self._make_card(cards, "Job Status", "No active job.")
        self.status_card.pack(side="left", fill="both", expand=True)
        self.export_card = self._make_card(cards, "SketchUp Handoff", "No export yet.")
        self.export_card.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.capture_card = self._make_card(
            cards,
            "Capture Notes",
            "Use at least 8 photos. Keep the object centered, keep camera height stable, and prefer a plain background.",
        )
        self.capture_card.pack(side="left", fill="both", expand=True, padx=(12, 0))

        notebook = ttk.Notebook(content)
        notebook.pack(fill="both", expand=True, pady=(14, 0))

        preview_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=14)
        notebook.add(preview_tab, text="Preview")
        self.preview_canvas = PreviewCanvas(preview_tab)
        self.preview_canvas.pack(fill="both", expand=True)

        logs_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=14)
        notebook.add(logs_tab, text="Logs")
        self.logs_text = tk.Text(logs_tab, wrap="word", bg="#fffdf8", fg="#2b241f", relief="flat", font=("Consolas", 10))
        self.logs_text.pack(fill="both", expand=True)
        self.logs_text.configure(state="disabled")

        photos_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=14)
        notebook.add(photos_tab, text="Photos")
        self.photos_list = tk.Listbox(photos_tab, bg="#fffdf8", fg="#2c241f", relief="flat", font=("Segoe UI", 10))
        self.photos_list.pack(fill="both", expand=True)

    def _make_card(self, master: tk.Misc, title: str, body: str) -> ttk.Frame:
        card = ttk.Frame(master, style="Panel.TFrame", padding=14)
        ttk.Label(card, text=title, style="Body.TLabel", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        body_label = ttk.Label(card, text=body, style="Sub.TLabel", wraplength=280, justify="left")
        body_label.pack(anchor="w", pady=(8, 0))
        card.body_label = body_label
        return card

    def _poll(self) -> None:
        try:
            self.refresh_projects(selected_only=True)
        finally:
            self.root.after(self.POLL_MS, self._poll)

    def refresh_projects(self, selected_only: bool = False) -> None:
        projects = store.list_projects()
        current_ids = set(self.project_tree.get_children())
        desired_ids = {project["id"] for project in projects}
        if not selected_only or current_ids != desired_ids:
            for item_id in current_ids - desired_ids:
                self.project_tree.delete(item_id)
            for project in projects:
                values = (project["status"],)
                if project["id"] in current_ids:
                    self.project_tree.item(project["id"], text=project["name"], values=values)
                else:
                    self.project_tree.insert("", "end", iid=project["id"], text=project["name"], values=values)

        if not self.selected_project_id and projects:
            self.selected_project_id = projects[0]["id"]
            self.project_tree.selection_set(self.selected_project_id)
        self._refresh_detail()

    def _on_project_selected(self) -> None:
        selection = self.project_tree.selection()
        self.selected_project_id = selection[0] if selection else None
        self._refresh_detail()

    def _refresh_detail(self) -> None:
        if not self.selected_project_id:
            return
        project = store.get_project(self.selected_project_id)
        if not project:
            return
        jobs = store.list_jobs(project["id"])
        latest_job = jobs[0] if jobs else None
        self.project_title.configure(text=project["name"])
        self.project_meta.configure(text=f"{project['backend']} backend • {project['status']} • {project['workspace_dir']}")

        status_text = "No job yet."
        if latest_job:
            percent = int(float(latest_job["progress"]) * 100)
            status_text = f"{latest_job['status']} • {latest_job['stage']} • {percent}%\n{latest_job['message']}"
        self.status_card.body_label.configure(text=status_text)

        export_text = "No export yet."
        if project.get("last_manifest_path"):
            export_text = (
                f"Latest manifest:\n{project['last_manifest_path']}\n\n"
                "In SketchUp use Plugins > Gaussian Points > Import Latest Companion Result."
            )
        self.export_card.body_label.configure(text=export_text)

        images = list_project_images(project["id"])
        self.photos_list.delete(0, tk.END)
        for image in images:
            self.photos_list.insert(tk.END, image.name)

        self._refresh_logs(latest_job)
        self._refresh_preview(project)

    def _refresh_logs(self, latest_job: dict | None) -> None:
        content = ""
        if latest_job and latest_job.get("log_path") and Path(latest_job["log_path"]).exists():
            content = Path(latest_job["log_path"]).read_text(encoding="utf-8")
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", tk.END)
        self.logs_text.insert("1.0", content)
        self.logs_text.see(tk.END)
        self.logs_text.configure(state="disabled")

    def _refresh_preview(self, project: dict) -> None:
        preview_path = project.get("last_result_ply")
        if not preview_path or not Path(preview_path).exists():
            if self.preview_path is not None:
                self.preview_path = None
                self.preview_canvas.set_points([])
            return
        if preview_path == self.preview_path:
            return
        try:
            points, stats = read_preview_points(preview_path)
        except Exception as error:
            self.preview_canvas.set_points([])
            self.capture_card.body_label.configure(text=f"Preview load failed: {error}")
            return
        self.preview_path = preview_path
        self.preview_canvas.set_points(points)
        self.capture_card.body_label.configure(
            text=(
                f"Previewing {stats['point_count']} exported splats in lightweight mode.\n"
                "The saved PLY keeps full gaussian data for the SketchUp renderer.\n"
                f"Bounds: {stats['bounds']['min']} -> {stats['bounds']['max']}"
            )
        )

    def _launch_worker(self, job_id: str) -> None:
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW
        worker_python = paths.preferred_worker_python()
        python_executable = str(worker_python or Path(sys.executable))
        repo_root = str(paths.repo_root())
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = repo_root if not pythonpath else f"{repo_root}{os.pathsep}{pythonpath}"
        subprocess.Popen(
            [python_executable, "-m", "companion_app.worker_entry", job_id],
            cwd=repo_root,
            env=env,
            creationflags=creationflags,
        )

    def create_project(self) -> None:
        name = simpledialog.askstring("Project Name", "Project name:", parent=self.root)
        if name is None:
            return
        media_paths = filedialog.askopenfilenames(
            title="Choose project media",
            filetypes=[
                ("Supported Media", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp *.mp4 *.mov *.m4v *.avi *.mkv *.webm *.zip"),
                ("Images", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                ("Videos", "*.mp4 *.mov *.m4v *.avi *.mkv *.webm"),
                ("Archives", "*.zip"),
            ],
        )
        if not media_paths:
            return
        project = store.create_project(name=name)
        ingest_media_sources(project["id"], list(media_paths))
        self.selected_project_id = project["id"]
        self.refresh_projects()
        self.project_tree.selection_set(project["id"])

    def _prompt_job_settings(self, force_restart: bool) -> dict | None:
        settings = store.default_job_settings(force_restart=force_restart)
        steps = simpledialog.askinteger(
            "Training Steps",
            "How many training steps should this run use?",
            parent=self.root,
            minvalue=200,
            maxvalue=20000,
            initialvalue=int(settings["train_steps"]),
        )
        if steps is None:
            return None
        settings["train_steps"] = int(steps)
        settings["densify_stop_iter"] = min(int(settings["densify_stop_iter"]), max(steps - 1, 1))
        settings["refine_scale2d_stop_iter"] = int(steps)
        return settings

    def _start_project_job(self, force_restart: bool) -> None:
        if not self.selected_project_id:
            return
        manifest_status = ensure_project_camera_manifests(self.selected_project_id)
        existing = store.latest_job(self.selected_project_id)
        if existing and existing["status"] == "running":
            messagebox.showinfo("Job Running", "This project already has a running job.")
            return
        settings = self._prompt_job_settings(force_restart=force_restart)
        if settings is None:
            return
        job = store.create_job(self.selected_project_id, settings)
        if manifest_status["mode"] != "manifest":
            messagebox.showwarning(
                "No Camera Manifest",
                "This project does not have a usable transforms.json camera manifest, so training will fall back to SfM-only cameras. Results may be much worse.",
            )
        self._launch_worker(job["id"])
        self.refresh_projects()

    def start_job(self) -> None:
        self._start_project_job(force_restart=False)

    def continue_job(self) -> None:
        self._start_project_job(force_restart=False)

    def restart_job(self) -> None:
        self._start_project_job(force_restart=True)

    def stop_job(self) -> None:
        if not self.selected_project_id:
            return
        latest_job = store.latest_job(self.selected_project_id)
        if not latest_job:
            return
        store.request_job_stop(latest_job["id"])
        self.refresh_projects()

    def open_latest_export(self) -> None:
        if not self.selected_project_id:
            return
        project = store.get_project(self.selected_project_id)
        if not project or not project.get("last_manifest_path"):
            return
        os.startfile(Path(project["last_manifest_path"]).parent)  # type: ignore[attr-defined]

    def open_data_folder(self) -> None:
        os.startfile(paths.data_root())  # type: ignore[attr-defined]


def launch(plugin_root: str | None = None) -> int:
    root = tk.Tk()
    CompanionApp(root, plugin_root=plugin_root)
    root.mainloop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-root", default=None)
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(APP_VERSION)
        return 0
    return launch(plugin_root=args.plugin_root)
