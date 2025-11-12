import os
from typing import Iterable, Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory

from code_puppy.config import get_global_model_name, set_model_name
from code_puppy.model_factory import ModelFactory


def load_model_names():
    """Load model names from the config that's fetched from the endpoint."""
    models_config = ModelFactory.load_config()
    return list(models_config.keys())


def get_active_model():
    """
    Returns the active model from the config using get_model_name().
    This ensures consistency across the codebase by always using the config value.
    """
    return get_global_model_name()


def set_active_model(model_name: str):
    """
    Sets the active model name by updating the config (for persistence).
    """
    set_model_name(model_name)
    # Reload the currently active agent so the new model takes effect immediately
    try:
        from code_puppy.agents import get_current_agent

        current_agent = get_current_agent()
        # JSON agents may need to refresh their config before reload
        if hasattr(current_agent, "refresh_config"):
            try:
                current_agent.refresh_config()
            except Exception:
                # Non-fatal, continue to reload
                ...
        current_agent.reload_code_generation_agent()
    except Exception:
        # Swallow errors to avoid breaking the prompt flow; model persists for next run
        pass


class ModelNameCompleter(Completer):
    """
    A completer that triggers on '/model' to show available models from models.json.
    Only '/model' (not just '/') will trigger the dropdown.
    """

    def __init__(self, trigger: str = "/model"):
        self.trigger = trigger
        self.model_names = load_model_names()

    def get_completions(
        self, document: Document, complete_event
    ) -> Iterable[Completion]:
        text = document.text
        cursor_position = document.cursor_position
        text_before_cursor = text[:cursor_position]

        # Only trigger if /model is at the very beginning of the line and has a space after it
        stripped_text = text_before_cursor.lstrip()
        if not stripped_text.startswith(self.trigger + " "):
            return

        # Find where /model actually starts (after any leading whitespace)
        symbol_pos = text_before_cursor.find(self.trigger)
        text_after_trigger = text_before_cursor[
            symbol_pos + len(self.trigger) + 1 :
        ].lstrip()
        start_position = -(len(text_after_trigger))

        # Filter model names based on what's typed after /model
        current_agent_model = None
        try:
            from code_puppy.agents import get_current_agent
            from code_puppy.config import get_agent_pinned_model

            current_agent = get_current_agent()
            current_agent_model = get_agent_pinned_model(current_agent.name)
        except Exception:
            pass  # Fail gracefully if agent info not available

        for model_name in self.model_names:
            if text_after_trigger and not model_name.startswith(text_after_trigger):
                continue  # Skip models that don't match the typed text

            # Determine the meta text based on model status
            if model_name == get_active_model():
                if current_agent_model == model_name:
                    meta = "Model (selected + pinned)"
                else:
                    meta = "Model (selected)"
            elif current_agent_model == model_name:
                meta = "Model (pinned to agent)"
            else:
                meta = "Model"
            yield Completion(
                model_name,
                start_position=start_position,
                display=model_name,
                display_meta=meta,
            )


def update_model_in_input(text: str) -> Optional[str]:
    # If input starts with /model or /m and a model name, set model and strip it out
    content = text.strip()

    # Check for /model command (require space after /model)
    if content.startswith("/model "):
        rest = content[7:].strip()  # Remove '/model '
        model_names = load_model_names()
        for model in model_names:
            if rest == model:
                # Check for pinned model on current agent
                if not _handle_pinned_model_change(model):
                    # User cancelled the change
                    return None

                set_active_model(model)
                # Remove /model from the input
                idx = text.find("/model " + model)
                if idx != -1:
                    new_text = (
                        text[:idx] + text[idx + len("/model " + model) :]
                    ).strip()
                    return new_text

    # Check for /m command
    elif content.startswith("/m "):
        rest = content[3:].strip()  # Remove '/m '
        model_names = load_model_names()
        for model in model_names:
            if rest == model:
                # Check for pinned model on current agent
                if not _handle_pinned_model_change(model):
                    # User cancelled the change
                    return None

                set_active_model(model)
                # Remove /m from the input
                idx = text.find("/m " + model)
                if idx != -1:
                    new_text = (text[:idx] + text[idx + len("/m " + model) :]).strip()
                    return new_text

    return None


def _handle_pinned_model_change(requested_model: str) -> bool:
    """Handle pinned model logic when switching models.

    Args:
        requested_model: The model the user wants to switch to

    Returns:
        bool: True to proceed with model change, False to cancel
    """
    try:
        from rich.console import Console

        from code_puppy.agents import get_current_agent
        from code_puppy.config import get_agent_pinned_model, set_agent_pinned_model

        current_agent = get_current_agent()
        current_agent_name = current_agent.name
        pinned_model = get_agent_pinned_model(current_agent_name)

        # If no pinned model or pinned model equals requested model, proceed normally
        if not pinned_model or pinned_model == requested_model:
            return True

        console = Console()
        console.print("\n[bold yellow]⚠️  Model Pinned Conflicting Change[/bold yellow]")
        console.print(
            f"Current agent '[cyan]{current_agent_name}[/cyan]' has model '[green]{pinned_model}[/green]' pinned."
        )
        console.print(f"You're trying to switch to '[cyan]{requested_model}[/cyan]'.\n")

        from rich.prompt import IntPrompt

        # Show the options with numbers for arrow key selection
        console.print("[bold]What would you like to do?[/bold]")
        console.print("[green]1.[/green] Change model but leave pin alone")
        console.print("[green]2.[/green] Change model and also update the pin")
        console.print("[green]3.[/green] Change model and unpin")
        console.print("[green]4.[/green] Cancel the change\n")

        choice = IntPrompt.ask(
            "Select an option", choices=[1, 2, 3, 4], default=1, show_choices=False
        )

        if choice == 1:
            # Change model but leave pin alone
            console.print(
                f"✅ Switching to '{requested_model}' (pin for '{current_agent_name}' remains '{pinned_model}')"
            )
            return True
        elif choice == 2:
            # Change model and also change the pin
            set_agent_pinned_model(current_agent_name, requested_model)
            console.print(
                f"✅ Switching to '{requested_model}' and updating pin for '{current_agent_name}'"
            )
            return True
        elif choice == 3:
            # Change model and unpin
            from code_puppy.config import clear_agent_pinned_model

            clear_agent_pinned_model(current_agent_name)
            console.print(
                f"✅ Switching to '{requested_model}' and unpinning from '{current_agent_name}'"
            )
            return True
        elif choice == 4:
            # Cancel the change
            console.print(f"❌ Cancelled model change to '{requested_model}'")
            return False

    except Exception:
        # If anything fails, let the change proceed (fail safe)
        return True

    return True


async def get_input_with_model_completion(
    prompt_str: str = ">>> ",
    trigger: str = "/model",
    history_file: Optional[str] = None,
) -> str:
    history = FileHistory(os.path.expanduser(history_file)) if history_file else None
    session = PromptSession(
        completer=ModelNameCompleter(trigger),
        history=history,
        complete_while_typing=True,
    )
    text = await session.prompt_async(prompt_str)
    possibly_stripped = update_model_in_input(text)
    if possibly_stripped is not None:
        return possibly_stripped
    return text
