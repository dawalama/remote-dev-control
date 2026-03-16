"""File watcher for auto-updating the knowledge index."""

import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .indexer import build_full_index
from .store import load_config, save_index


class KnowledgeFileHandler(FileSystemEventHandler):
    """Handles file changes in .ai/ directories."""
    
    def __init__(self, debounce_seconds: float = 2.0):
        self.debounce_seconds = debounce_seconds
        self.last_rebuild = 0.0
    
    def _should_rebuild(self, path: str) -> bool:
        if not path.endswith(".md"):
            return False
        if ".git" in path:
            return False
        return True
    
    def _rebuild_index(self):
        now = time.time()
        if now - self.last_rebuild < self.debounce_seconds:
            return
        
        self.last_rebuild = now
        config = load_config()
        index = build_full_index(config)
        save_index(index)
        print(f"[{time.strftime('%H:%M:%S')}] Index rebuilt")
    
    def on_modified(self, event: FileSystemEvent):
        if not event.is_directory and self._should_rebuild(event.src_path):
            self._rebuild_index()
    
    def on_created(self, event: FileSystemEvent):
        if not event.is_directory and self._should_rebuild(event.src_path):
            self._rebuild_index()
    
    def on_deleted(self, event: FileSystemEvent):
        if not event.is_directory and self._should_rebuild(event.src_path):
            self._rebuild_index()


def watch_knowledge_dirs():
    """Watch all .ai/ directories for changes and rebuild index."""
    config = load_config()
    
    paths_to_watch: list[Path] = []
    
    if config.global_ai_dir.exists():
        paths_to_watch.append(config.global_ai_dir)
    
    for project in config.projects:
        ai_path = project.full_ai_path
        if ai_path.exists():
            paths_to_watch.append(ai_path)
    
    if not paths_to_watch:
        print("No .ai/ directories to watch")
        return
    
    event_handler = KnowledgeFileHandler()
    observer = Observer()
    
    for path in paths_to_watch:
        observer.schedule(event_handler, str(path), recursive=True)
        print(f"Watching: {path}")
    
    observer.start()
    print("\nPress Ctrl+C to stop watching\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nStopped watching")
    
    observer.join()
