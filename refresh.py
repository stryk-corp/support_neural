from pathlib import Path

from .config import Settings
from .kb import SQLiteKBStore, refresh_from_files, refresh_workflow_library


def main():
    settings = Settings.from_env()
    store = SQLiteKBStore(settings.database_path)
    result = refresh_from_files(
        store,
        settings.kb_path,
        settings.approved_paths_path,
    )
    library = refresh_workflow_library(
        store,
        Path(__file__).with_name('knowledge') / 'neuralspace_workflows.json',
        tenant_id=getattr(settings, 'tenant_id', 'neuralvillage'),
        scope_id='public',
    )
    print(
        f'Indexed {result["documents"]} approved public facts and '
        f'{library["documents"]} NeuralSpace workflow documents without calling Gemini.'
    )


if __name__ == '__main__':
    main()
