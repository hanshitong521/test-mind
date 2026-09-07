Feature: 红包业务 Karate 场景冒烟（TestMind Karate adapter 真实执行证明）

  Background:
    * url 'http://127.0.0.1:18101'

  Scenario: 合法创建返回 201 且 DB 语义字段合理
    Given path '/red-packets'
    And request { name: 'karate-smoke', amount_cents: 100, quantity: 5, influencer_id: 1, created_by: 1 }
    When method post
    Then status 201
    And match response.id == '#number'

  Scenario: 金额越界必须 400
    Given path '/red-packets'
    And request { name: 'karate-bad', amount_cents: 100001, quantity: 5, influencer_id: 1, created_by: 1 }
    When method post
    Then status 400

  Scenario: 幂等重放第二次返回 200 duplicate
    def key = karate.uuid()
    Given path '/red-packets'
    And request { name: 'karate-idem', amount_cents: 100, quantity: 5, influencer_id: 1, created_by: 1, idem_key: '#(key)' }
    When method post
    Then status 201
    Given path '/red-packets'
    And request { name: 'karate-idem', amount_cents: 100, quantity: 5, influencer_id: 1, created_by: 1, idem_key: '#(key)' }
    When method post
    Then status 200
    And match response.duplicate == true

  Scenario: 状态机 CREATED 直接领取被拒
    Given path '/red-packets'
    And request { name: 'karate-state', amount_cents: 100, quantity: 5, influencer_id: 1, created_by: 1 }
    When method post
    Then status 201
    Given path '/red-packets/', response.id, '/grant'
    When method post
    Then status 409
