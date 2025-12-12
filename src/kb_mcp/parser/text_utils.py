"""Text utility functions for parsers."""

import re


def clean_text(text: str) -> str:
    """Clean extracted text - remove latex formulas etc.
    
    Args:
        text: Raw extracted text
        
    Returns:
        Cleaned text
    """
    # Remove latex formulas
    text = re.sub(r'<latexit[^>]*>.*?</latexit>', '[equation]', text)
    return text


def slides_format_as_markdown(text: str) -> str:
    """Format slide-like text as markdown.
    
    Args:
        text: Slide-like text to format
        
    Returns:
        Formatted markdown text
    """
    lines = text.split('\n')
    formatted_lines = []
    in_list = False

    for line in lines:
        line = line.strip()
        if not line:
            if in_list:
                formatted_lines.append('')
            in_list = False
            continue

        # Make first non-empty line a title
        if not formatted_lines:
            formatted_lines.append(f'# {line}')
            continue

        # Check for bullet points
        if line.startswith('•') or line.startswith('-') or line.startswith('●'):
            if not line[1:].strip():
                continue
            formatted_lines.append(f'- {line[1:].strip()}')
            in_list = True
        elif in_list and not line[0].isupper():
            # Continuation of previous bullet point
            formatted_lines[-1] += f' {line}'
        elif line.startswith('○'):  # Second order list
            if not line[1:].strip():
                continue
            formatted_lines.append(f'    - {line[1:].strip()}')
            in_list = True
        else:
            # Regular text
            formatted_lines.append(line)
            in_list = False

    return '\n'.join(formatted_lines)

