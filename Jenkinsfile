pipeline {
    agent any

    environment {
        // Local virtual environment within the Jenkins workspace for portability
        VENV_PATH = "${WORKSPACE}/.venv"
        VENV_BIN = "${VENV_PATH}/bin"
    }

    stages {
        stage('Initialize') {
            steps {
                echo 'Creating Isolated Virtual Environment...'
                sh "python3 -m venv ${VENV_PATH}"

                echo 'Installing Dependencies...'
                sh "${VENV_BIN}/pip install --upgrade pip"
                // Install our specific logflow from GitHub to avoid public PyPI confusion
                sh "${VENV_BIN}/pip install git+https://github.com/Gearlux/logflow.git@main"
                sh "${VENV_BIN}/pip install -e .[dev]"
            }
        }

        stage('Quality Gates') {
            parallel {
                stage('Black') {
                    steps {
                        script {
                            def rc = sh(script: "${VENV_BIN}/black --check --diff confluid tests examples > black-output.txt 2>&1", returnStatus: true)
                            sh """${VENV_BIN}/python3 -c "
import xml.etree.ElementTree as ET
rc = ${rc}
root = ET.Element('testsuite', name='black', tests='1', failures=str(min(rc, 1)))
tc = ET.SubElement(root, 'testcase', classname='black', name='formatting-check')
if rc != 0:
    with open('black-output.txt') as f:
        ET.SubElement(tc, 'failure', message='Black formatting issues found').text = f.read()
ET.ElementTree(root).write('black-report.xml', xml_declaration=True, encoding='unicode')
" """
                        }
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'black-report.xml'
                        }
                    }
                }
                stage('Isort') {
                    steps {
                        script {
                            def rc = sh(script: "${VENV_BIN}/isort --check-only --diff confluid tests examples > isort-output.txt 2>&1", returnStatus: true)
                            sh """${VENV_BIN}/python3 -c "
import xml.etree.ElementTree as ET
rc = ${rc}
root = ET.Element('testsuite', name='isort', tests='1', failures=str(min(rc, 1)))
tc = ET.SubElement(root, 'testcase', classname='isort', name='import-order-check')
if rc != 0:
    with open('isort-output.txt') as f:
        ET.SubElement(tc, 'failure', message='Isort import order issues found').text = f.read()
ET.ElementTree(root).write('isort-report.xml', xml_declaration=True, encoding='unicode')
" """
                        }
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'isort-report.xml'
                        }
                    }
                }
                stage('Flake8') {
                    steps {
                        sh "rm -f flake8.txt flake8-report.xml"
                        sh "${VENV_BIN}/flake8 confluid tests examples --tee --output-file=flake8.txt || true"
                        sh "if [ -f flake8.txt ]; then ${VENV_BIN}/flake8_junit flake8.txt flake8-report.xml; fi"
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'flake8-report.xml'
                        }
                    }
                }
                stage('Mypy') {
                    steps {
                        sh "${VENV_BIN}/mypy confluid tests examples --junit-xml=mypy-report.xml || true"
                    }
                    post {
                        always {
                            junit allowEmptyResults: true, testResults: 'mypy-report.xml'
                        }
                    }
                }
            }
        }

        stage('Unit Tests') {
            steps {
                sh "${VENV_BIN}/pytest tests --junitxml=test-report.xml --cov=confluid --cov-report=xml:coverage.xml --cov-report=term"
            }
            post {
                always {
                    // Archive and display JUnit test results
                    junit allowEmptyResults: true, testResults: 'test-report.xml'
                    
                    // Display Coverage in Jenkins UI using Code Coverage API Plugin
                    recordCoverage tools: [[parser: 'COBERTURA', pattern: 'coverage.xml']]
                }
            }
        }

        stage('Verify Examples') {
            steps {
                echo 'Running project examples...'
                // Use single quotes for the shell command to prevent Groovy from trying to resolve $f
                sh '''
                    for f in examples/*.py; do
                        echo "Running $f..."
                        ${VENV_BIN}/python3 "$f"
                    done
                '''
            }
        }
    }

    post {
        always {
            echo 'Confluid Pipeline Complete.'
        }
        success {
            echo 'Confluid is healthy and ready for publication.'
        }
        failure {
            echo 'Confluid build failed. Please check linting or test failures.'
        }
    }
}
