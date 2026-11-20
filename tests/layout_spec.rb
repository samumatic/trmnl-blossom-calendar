# frozen_string_literal: true

require_relative 'support/layout_checks'

# Data that stretches the layout, drawn in every view on TRMNL OG and TRMNL X, landscape and portrait.
# The checks are in tests/support/layout_checks.rb; the clock and the calendar data are as in plugin_spec.rb.
RSpec.describe 'Blossom Calendar layout' do
  def fixture(name) = JSON.parse(File.read(File.join(__dir__, 'fixtures', name)))

  let(:now) { Time.utc(2026, 11, 9, 7) } # Monday 08:00 in Berlin
  let(:variables) { { 'trmnl' => { 'user' => { 'utc_offset' => 3600, 'time_zone_iana' => 'Europe/Berlin', 'locale' => 'de' } } } }
  let(:custom_fields) { { 'calendar_1' => 'caldav_1' } }

  def calendar(name, **fields) = { now:, variables:, data: { 'caldav_1' => fixture(name) }, custom_fields: custom_fields.merge(fields) }

  # Titles that wrap, a word longer than a line, a long title, 12-hour times and a two-week event.
  def long_text = calendar('long-text.json', 'calendar_name' => 'Familienkalender der Familie Schmidt-Oberhausen')
  # The same in English.
  def long_text_en = long_text.merge(custom_fields: long_text[:custom_fields].merge('language' => 'en'))
  # Four events a day for two weeks: more than any view holds.
  def many = calendar('many-events.json', 'days_ahead' => '14')
  # A calendar with nothing on.
  def nothing = { now:, variables:, data: { 'caldav_1' => fixture('caldav.json').merge('events' => []) }, custom_fields: }
  # A calendar plugin that has sent nothing yet.
  def no_data = { now:, variables:, data: {}, custom_fields: }

  it_behaves_like 'a layout that holds up',
                  'long titles' => :long_text, 'long English titles' => :long_text_en, 'more events than fit' => :many,
                  'a free week' => :nothing, 'no calendar data yet' => :no_data
end
