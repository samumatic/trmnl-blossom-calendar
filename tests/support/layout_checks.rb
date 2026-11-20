# frozen_string_literal: true

# Layout checks for every view on every screen, with the edge cases a plugin's tests/layout_spec.rb names.
# The same file is in each of Samu's TRMNL plugins; change it in all of them.
#
# 'a publishable recipe' only checks for page errors and leaked values. It does not look for text cut off at a box's
# edge, and nothing checks that a list's icon lines up with its text. Both only show with data that stretches the
# layout: a title that wraps, an empty list, a list longer than the screen.

LAYOUT_VIEWS = %w[full half_horizontal half_vertical quadrant].freeze
LAYOUT_SCREENS = [
  { device: 'og_plus' }, { device: 'og_plus', orientation: :portrait },
  { device: 'v2' }, { device: 'v2', orientation: :portrait }
].freeze

# For each .item with an icon in front of its text, the icon's middle must be on the first line of that text.
# A row's icon is centred on the whole row by default, so it drifts down between the lines once the text wraps.
ICON_ON_FIRST_LINE_SCRIPT = <<~JS
  (() => {
    const visible = (r) => r.width > 0 && r.height > 0;
    const firstLine = (item, after) => {
      const walker = document.createTreeWalker(item, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (!node.textContent.trim() || after.contains(node)) continue;
        if (!(after.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING)) continue;
        const range = document.createRange();
        range.selectNodeContents(node);
        const line = [...range.getClientRects()].find(visible);
        if (line) return { line, text: node.textContent.trim().slice(0, 40) };
      }
      return null;
    };
    const found = [];
    document.querySelectorAll('.item').forEach((item) => {
      const icon = item.querySelector('.icon, svg, img');
      if (!icon) return;
      const box = icon.getBoundingClientRect();
      const first = visible(box) && firstLine(item, icon);
      if (!first || box.right > first.line.left + 1) return; // no icon in front of text
      const off = (box.top + box.bottom) / 2 - (first.line.top + first.line.bottom) / 2;
      if (Math.abs(off) > Math.max(2, first.line.height / 4)) {
        found.push(`the icon of "${first.text}" sits ${Math.round(off)}px ${off > 0 ? 'below' : 'above'} the middle of its first line`);
      }
    });
    return found;
  })()
JS

RSpec::Matchers.define :have_icons_on_their_first_line do
  match { |screen| (@misaligned = screen.evaluate(ICON_ON_FIRST_LINE_SCRIPT)).empty? }
  failure_message { |screen| "expected each list icon on its text's first line on #{screen.inspect}, but #{@misaligned.join('; ')}" }
end

def layout_screen_name(screen) = [screen[:device] == 'v2' ? 'TRMNL X' : 'TRMNL OG', screen[:orientation] || :landscape].join(' ')

# cases: { 'a title that wraps' => :method_returning_render_inputs, ... }
RSpec.shared_examples 'a layout that holds up' do |cases|
  cases.each do |name, inputs|
    LAYOUT_VIEWS.product(LAYOUT_SCREENS).each do |view, screen|
      it "draws #{name} in the #{view} view on #{layout_screen_name(screen)} without clipping or misalignment" do
        drawn = trmnl.render(view:, **screen, **send(inputs))
        expect(drawn).to have_no_problems.and(have_no_leaked_text).and(have_no_overflow)
          .and(have_no_overlapping_text).and(have_no_text_clamped_with_room_to_spare)
          .and(have_icons_on_their_first_line)
      end
    end
  end
end
