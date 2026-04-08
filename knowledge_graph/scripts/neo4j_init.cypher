// Neo4j initialization script for the HIV/AIDS consultation knowledge graph.
// This script is designed for Neo4j 5.x.
// It creates node uniqueness constraints and high-value lookup indexes.

// ---------------------------------------------------------------------------
// Core evidence layer
// ---------------------------------------------------------------------------

CREATE CONSTRAINT guideline_document_id IF NOT EXISTS
FOR (n:GuidelineDocument)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT guideline_section_id IF NOT EXISTS
FOR (n:GuidelineSection)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT evidence_span_id IF NOT EXISTS
FOR (n:EvidenceSpan)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT assertion_id IF NOT EXISTS
FOR (n:Assertion)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT recommendation_id IF NOT EXISTS
FOR (n:Recommendation)
REQUIRE n.id IS UNIQUE;

// ---------------------------------------------------------------------------
// Core clinical fact layer
// ---------------------------------------------------------------------------

CREATE CONSTRAINT disease_id IF NOT EXISTS
FOR (n:Disease)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT disease_phase_id IF NOT EXISTS
FOR (n:DiseasePhase)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT pathogen_id IF NOT EXISTS
FOR (n:Pathogen)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT symptom_id IF NOT EXISTS
FOR (n:Symptom)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT sign_id IF NOT EXISTS
FOR (n:Sign)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT clinical_attribute_id IF NOT EXISTS
FOR (n:ClinicalAttribute)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT lab_test_id IF NOT EXISTS
FOR (n:LabTest)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT lab_finding_id IF NOT EXISTS
FOR (n:LabFinding)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT diagnostic_criterion_id IF NOT EXISTS
FOR (n:DiagnosticCriterion)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT opportunistic_infection_id IF NOT EXISTS
FOR (n:OpportunisticInfection)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT comorbidity_id IF NOT EXISTS
FOR (n:Comorbidity)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT tumor_id IF NOT EXISTS
FOR (n:Tumor)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT syndrome_or_complication_id IF NOT EXISTS
FOR (n:SyndromeOrComplication)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT exposure_scenario_id IF NOT EXISTS
FOR (n:ExposureScenario)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT population_group_id IF NOT EXISTS
FOR (n:PopulationGroup)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT risk_factor_id IF NOT EXISTS
FOR (n:RiskFactor)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT transmission_route_id IF NOT EXISTS
FOR (n:TransmissionRoute)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT management_action_id IF NOT EXISTS
FOR (n:ManagementAction)
REQUIRE n.id IS UNIQUE;

// ---------------------------------------------------------------------------
// Core intervention layer
// ---------------------------------------------------------------------------

CREATE CONSTRAINT medication_id IF NOT EXISTS
FOR (n:Medication)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT drug_class_id IF NOT EXISTS
FOR (n:DrugClass)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT treatment_regimen_id IF NOT EXISTS
FOR (n:TreatmentRegimen)
REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT prevention_strategy_id IF NOT EXISTS
FOR (n:PreventionStrategy)
REQUIRE n.id IS UNIQUE;

// ---------------------------------------------------------------------------
// Name indexes for retrieval
// ---------------------------------------------------------------------------

CREATE INDEX guideline_document_name IF NOT EXISTS
FOR (n:GuidelineDocument)
ON (n.name);

CREATE INDEX guideline_section_name IF NOT EXISTS
FOR (n:GuidelineSection)
ON (n.name);

CREATE INDEX disease_name IF NOT EXISTS
FOR (n:Disease)
ON (n.name);

CREATE INDEX disease_phase_name IF NOT EXISTS
FOR (n:DiseasePhase)
ON (n.name);

CREATE INDEX pathogen_name IF NOT EXISTS
FOR (n:Pathogen)
ON (n.name);

CREATE INDEX symptom_name IF NOT EXISTS
FOR (n:Symptom)
ON (n.name);

CREATE INDEX sign_name IF NOT EXISTS
FOR (n:Sign)
ON (n.name);

CREATE INDEX clinical_attribute_name IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.name);

CREATE INDEX lab_test_name IF NOT EXISTS
FOR (n:LabTest)
ON (n.name);

CREATE INDEX lab_finding_name IF NOT EXISTS
FOR (n:LabFinding)
ON (n.name);

CREATE INDEX diagnostic_criterion_name IF NOT EXISTS
FOR (n:DiagnosticCriterion)
ON (n.name);

CREATE INDEX opportunistic_infection_name IF NOT EXISTS
FOR (n:OpportunisticInfection)
ON (n.name);

CREATE INDEX comorbidity_name IF NOT EXISTS
FOR (n:Comorbidity)
ON (n.name);

CREATE INDEX tumor_name IF NOT EXISTS
FOR (n:Tumor)
ON (n.name);

CREATE INDEX syndrome_or_complication_name IF NOT EXISTS
FOR (n:SyndromeOrComplication)
ON (n.name);

CREATE INDEX exposure_scenario_name IF NOT EXISTS
FOR (n:ExposureScenario)
ON (n.name);

CREATE INDEX population_group_name IF NOT EXISTS
FOR (n:PopulationGroup)
ON (n.name);

CREATE INDEX risk_factor_name IF NOT EXISTS
FOR (n:RiskFactor)
ON (n.name);

CREATE INDEX transmission_route_name IF NOT EXISTS
FOR (n:TransmissionRoute)
ON (n.name);

CREATE INDEX management_action_name IF NOT EXISTS
FOR (n:ManagementAction)
ON (n.name);

CREATE INDEX medication_name IF NOT EXISTS
FOR (n:Medication)
ON (n.name);

CREATE INDEX drug_class_name IF NOT EXISTS
FOR (n:DrugClass)
ON (n.name);

CREATE INDEX treatment_regimen_name IF NOT EXISTS
FOR (n:TreatmentRegimen)
ON (n.name);

CREATE INDEX prevention_strategy_name IF NOT EXISTS
FOR (n:PreventionStrategy)
ON (n.name);

CREATE INDEX recommendation_name IF NOT EXISTS
FOR (n:Recommendation)
ON (n.name);

// ---------------------------------------------------------------------------
// High-value semantic indexes
// ---------------------------------------------------------------------------

CREATE INDEX disease_canonical_name IF NOT EXISTS
FOR (n:Disease)
ON (n.canonical_name);

CREATE INDEX symptom_canonical_name IF NOT EXISTS
FOR (n:Symptom)
ON (n.canonical_name);

CREATE INDEX sign_canonical_name IF NOT EXISTS
FOR (n:Sign)
ON (n.canonical_name);

CREATE INDEX medication_canonical_name IF NOT EXISTS
FOR (n:Medication)
ON (n.canonical_name);

CREATE INDEX pathogen_canonical_name IF NOT EXISTS
FOR (n:Pathogen)
ON (n.canonical_name);

CREATE INDEX recommendation_number IF NOT EXISTS
FOR (n:Recommendation)
ON (n.recommendation_no);

CREATE INDEX recommendation_grade IF NOT EXISTS
FOR (n:Recommendation)
ON (n.evidence_grade);

CREATE INDEX recommendation_strength IF NOT EXISTS
FOR (n:Recommendation)
ON (n.strength);

CREATE INDEX assertion_predicate IF NOT EXISTS
FOR (n:Assertion)
ON (n.predicate);

CREATE INDEX evidence_span_lines IF NOT EXISTS
FOR (n:EvidenceSpan)
ON (n.line_start, n.line_end);

CREATE INDEX clinical_attribute_slot_key IF NOT EXISTS
FOR (n:ClinicalAttribute)
ON (n.slot_key);

CREATE INDEX lab_finding_semantic IF NOT EXISTS
FOR (n:LabFinding)
ON (n.test_id, n.operator, n.value, n.unit);

CREATE INDEX treatment_regimen_type IF NOT EXISTS
FOR (n:TreatmentRegimen)
ON (n.regimen_type);

CREATE INDEX medication_class_hint IF NOT EXISTS
FOR (n:Medication)
ON (n.drug_class);

