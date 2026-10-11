"""Shared, pre-paint theme initialization for public static pages."""
import re


def theme_head_script() -> str:
    return """<script data-st-theme="head">
(function(){var theme='dark';try{var saved=localStorage.getItem('dashboardTheme');if(saved==='light'||saved==='dark')theme=saved;}catch(e){}document.documentElement.dataset.theme=theme;document.documentElement.style.colorScheme=theme;})();
</script><style data-st-theme="palette">html[data-theme="dark"]{background:#101824;color:#e8eef7;--bg:#101824;--panel:#182332;--ink:#e8eef7;--muted:#a4b3c5;--line:#2d3b4e;--teal:#65d5cc;--blue:#90b0f7;--soft:#22344f;--warn:#e8bb7c}html[data-theme="light"]{background:#f6f8fb;color:#192c41}</style>"""


def theme_toggle_script() -> str:
    return """<script data-st-theme="toggle">
(function(){
  function apply(theme,save){
    const button=document.getElementById('themeToggle')||document.getElementById('theme');
    const dark=theme==='dark';
    document.documentElement.dataset.theme=theme;
    document.documentElement.style.colorScheme=theme;
    document.body.classList.toggle('dark',dark);
    if(button){button.textContent=dark?'라이트모드':'다크모드';button.setAttribute('aria-pressed',String(dark));}
    if(save)try{localStorage.setItem('dashboardTheme',theme);}catch(e){}
  }
  // Capture also covers preserved legacy pages whose own scripts bind a toggle.
  // Delegation survives an old explorer script replacing the header element.
  document.addEventListener('click',event=>{if(!event.target.closest('#themeToggle,#theme'))return;event.stopImmediatePropagation();apply(document.documentElement.dataset.theme==='dark'?'light':'dark',true);},true);
  window.addEventListener('storage',event=>{if(event.key==='dashboardTheme'||event.key===null)apply(event.newValue==='light'?'light':'dark',false);});
  document.addEventListener('DOMContentLoaded',()=>apply(document.documentElement.dataset.theme==='light'?'light':'dark',false),{once:true});
  apply(document.documentElement.dataset.theme==='light'?'light':'dark',false);
})();
</script>"""


def decorate_theme(text: str) -> str:
    """Refresh only theme code, preserving inline data and existing page behavior."""
    def remove_script(match):
        tag = match.group(0)
        if 'application/json' in tag or re.search(r'\bsrc\s*=', tag):
            return tag
        if 'data-st-theme=' in tag or ('dashboardTheme' in tag and 'document.documentElement.dataset.theme' in tag):
            return ''
        return tag
    text = re.sub(r'<script\b[^>]*>.*?</script>', remove_script, text, flags=re.S)
    text = re.sub(r'<style data-st-theme="palette">.*?</style>', '', text, flags=re.S)
    head = theme_head_script()
    if re.search(r'<meta\b[^>]*charset[^>]*>', text, flags=re.I):
        text = re.sub(r'(<meta\b[^>]*charset[^>]*>)', lambda m: m.group(1) + head, text, count=1, flags=re.I)
    elif re.search(r'<head\b[^>]*>', text, flags=re.I):
        text = re.sub(r'(<head\b[^>]*>)', lambda m: m.group(1) + head, text, count=1, flags=re.I)
    else:
        # Standalone historical reports sometimes have implicit head/body tags.
        text = re.sub(r'(<html\b[^>]*>)', lambda m: m.group(1) + head, text, count=1, flags=re.I)
    toggle = theme_toggle_script()
    return text.replace('</body>', toggle + '</body>', 1) if '</body>' in text else text + toggle
